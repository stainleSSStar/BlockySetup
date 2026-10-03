import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('updater', Path(__file__).parents[1] / 'scripts/update.py')
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


def container(name):
    repo, target, volume = u.SERVICES[name]
    mounts = [{'Type': 'volume', 'Name': volume, 'Source': '/docker/' + volume,
               'Destination': target, 'RW': True}]
    if name == 'blocky':
        mounts += [{'Type': 'bind', 'Source': '/opt/blocky/config.yml', 'Destination': '/app/config.yml', 'RW': False},
                   {'Type': 'bind', 'Source': '/root/blocky', 'Destination': '/app/lists', 'RW': False}]
    return {'Id': 'a' * 64, 'Image': 'sha256:old-' + name,
            'Config': {'Image': repo + ':1.2.3', 'Hostname': 'a' * 12,
                       'Env': ['SECRET=keep-me', 'TZ=Europe/Warsaw'], 'Cmd': ['--custom'],
                       'Entrypoint': ['/old'], 'Labels': {}},
            'HostConfig': {'NetworkMode': 'bridge' if name == 'blocky-db' else 'host',
                           'Binds': ['/root/blocky:/app/lists:ro'] if name == 'blocky' else None,
                           'RestartPolicy': {'Name': 'unless-stopped'},
                           'LogConfig': {'Type': 'json-file', 'Config': {'max-size': '10m'}},
                           'PortBindings': {'3306/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '3307'}]}},
            'Mounts': mounts, 'State': {'Running': True}}


class UpdateTests(unittest.TestCase):
    def test_payload_preserves_lists_credentials_network_and_volumes(self):
        c = container('blocky')
        original = copy.deepcopy(c)
        old = {'Config': {'Cmd': [], 'Entrypoint': ['/old']}}
        new = {'Id': 'sha256:new', 'Config': {'Cmd': [], 'Entrypoint': ['/new'], 'Env': ['NEW=yes']}}
        payload = u.create_payload(c, old, new)
        self.assertEqual(payload['Image'], 'sha256:new')
        self.assertEqual(payload['Cmd'], ['--custom'])
        self.assertEqual(payload['Entrypoint'], ['/new'])
        self.assertIn('SECRET=keep-me', payload['Env'])
        self.assertIn('NEW=yes', payload['Env'])
        self.assertNotIn('Hostname', payload)
        mounts = {m['Target']: m for m in payload['HostConfig']['Mounts']}
        self.assertEqual(mounts['/app/cache']['Source'], 'blocky_cache')
        self.assertEqual(mounts['/app/lists']['Source'], '/root/blocky')
        self.assertTrue(mounts['/app/lists']['ReadOnly'])
        self.assertEqual(payload['HostConfig']['RestartPolicy'], c['HostConfig']['RestartPolicy'])
        self.assertIsNone(payload['HostConfig']['Binds'])
        self.assertEqual(c, original)

    def test_ports_and_log_limits_preserved(self):
        c = container('blocky-db')
        payload = u.create_payload(c, {'Config': {}}, {'Id': 'new', 'Config': {}})
        for key in ('PortBindings', 'LogConfig', 'NetworkMode'):
            self.assertEqual(payload['HostConfig'][key], c['HostConfig'][key])

    def test_unrelated_container_and_wrong_volume_rejected(self):
        c = container('blocky')
        c['Config']['Image'] = 'unrelated:1.2.3'
        with self.assertRaises(ValueError):
            u.validate_container('blocky', c)
        c = container('blocky')
        c['Mounts'][0]['Name'] = 'unrelated'
        with self.assertRaises(ValueError):
            u.validate_container('blocky', c)

    def test_managed_image_id_supported(self):
        c = container('blocky-db')
        c['Config']['Image'] = 'sha256:123'
        c['Config']['Labels'] = {'io.blockysetup.managed': 'true'}
        u.validate_container('blocky-db', c)

    def test_database_stays_in_series_and_ignores_rc(self):
        pages = [{'results': [{'name': '11.4.9'}, {'name': '11.4.14-rc'}, {'name': '12.1.1'}],
                  'next': 'https://hub.docker.com/page2'},
                 {'results': [{'name': '11.4.13'}], 'next': None}]
        with patch.object(u, 'get_json', side_effect=pages):
            self.assertEqual(u.database_tag('11.4'), '11.4.13')
        with self.assertRaises(ValueError):
            u.version('v1.2.3-rc1')

    def simulate(self, backup_failure=False, validate_failure=False, create_failure=False, ready_failure=False, unchanged=False):
        calls = []
        containers = {name: container(name) for name in u.SERVICES}
        def fake_inspect(kind, name):
            if kind == 'container':
                return containers[name]
            is_old = name.startswith('sha256:old-')
            return {'Id': name if is_old else ('sha256:old-' + next(n for n, (repo, _, _) in u.SERVICES.items() if name.startswith(repo + ':'))) if unchanged else 'sha256:new', 'Config': {}}
        def fake_docker(*args, **kwargs):
            calls.append(args)
            if args[:3] == ('exec', 'blocky-db', 'mariadbd'):
                return types.SimpleNamespace(stdout='mariadbd Ver 11.4.13-MariaDB')
            if args[0] == 'run' and validate_failure:
                raise subprocess.CalledProcessError(1, args)
            if args[:3] == ('exec', 'blocky-db', 'sh'):
                if backup_failure:
                    raise subprocess.CalledProcessError(1, args)
                kwargs['stdout'].write(b'-- SQL backup')
            return types.SimpleNamespace(stdout='1.51')
        releases = {'blocky': 'v1.2.4', 'prometheus': 'v1.2.4', 'grafana': 'v1.2.4'}
        def fake_json(url):
            repo = url.split('/')[-3]
            return {'tag_name': releases[repo], 'prerelease': False, 'draft': False}
        with tempfile.TemporaryDirectory() as directory:
            real_path = Path
            def fake_path(path):
                return real_path(directory) if path == '/opt/blocky-backups' else real_path(path)
            with patch.object(u, 'inspect', side_effect=fake_inspect), patch.object(u, 'docker', side_effect=fake_docker), \
                 patch.object(u, 'database_tag', return_value='11.4.13'), patch.object(u, 'get_json', side_effect=fake_json), \
                 patch.object(u, 'Path', side_effect=fake_path), patch.object(u.shutil, 'copytree'), patch.object(u.shutil, 'copy2'), \
                 patch.object(u, 'create_container', return_value='new-id', side_effect=RuntimeError('create failed') if create_failure else None), patch.object(u, 'ready', side_effect=RuntimeError('not ready') if ready_failure else None):
                if backup_failure or validate_failure or create_failure or ready_failure:
                    with self.assertRaises((subprocess.CalledProcessError, RuntimeError)):
                        u.run_update(types.SimpleNamespace(check=False))
                else:
                    u.run_update(types.SimpleNamespace(check=False))
        return calls

    def test_create_failure_restores_previous_container(self):
        calls = self.simulate(create_failure=True)
        renames = [c for c in calls if c[0] == 'rename']
        self.assertEqual(renames[-1][-1], 'blocky-db')
        self.assertIn(('start', 'blocky-db'), calls)
        self.assertEqual(len([c for c in calls if c[0] == 'stop']), 1)

    def test_failed_ready_does_not_start_old_database_on_migrated_volume(self):
        calls = self.simulate(ready_failure=True)
        self.assertNotIn(('start', 'blocky-db'), calls)
        self.assertEqual(len([c for c in calls if c[0] == 'stop']), 1)

    def test_unchanged_images_skip_backups_and_restarts(self):
        calls = self.simulate(unchanged=True)
        self.assertFalse(any(c[0] in ('stop', 'run', 'rename', 'start') for c in calls))
        self.assertFalse(any(c[:3] == ('exec', 'blocky-db', 'sh') for c in calls))

    def test_failed_backup_never_stops_services(self):
        calls = self.simulate(backup_failure=True)
        self.assertFalse(any(c[0] in ('stop', 'rename', 'start') for c in calls))

    def test_failed_validation_never_stops_services(self):
        calls = self.simulate(validate_failure=True)
        self.assertFalse(any(c[0] in ('stop', 'rename', 'start') for c in calls))
        self.assertTrue(any('--volumes-from' in c and 'blocky:ro' in c for c in calls))

    def test_backup_precedes_replacement_and_old_containers_retained(self):
        calls = self.simulate()
        dump_index = next(i for i, c in enumerate(calls) if c[:3] == ('exec', 'blocky-db', 'sh'))
        stop_index = next(i for i, c in enumerate(calls) if c[0] == 'stop')
        self.assertLess(dump_index, stop_index)
        self.assertFalse(any(c[0] in ('rm', 'volume') for c in calls))
        self.assertEqual([c[-1] for c in calls if c[0] == 'stop'], list(u.SERVICES))


if __name__ == '__main__':
    unittest.main()
