import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
MOCK = '''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['ENGINE_TEST_LOG'], 'a') as f:
    f.write(json.dumps([name, *args]) + '\\n')
mode = os.environ.get('ENGINE_TEST_MODE', '')
if name == 'dpkg-query':
    if args[-1] in ('docker-ce', 'docker-ce-cli'):
        print('installed\\t2.0.0')
    elif args[0] == '-W' and len(args) > 3:
        print('docker-ce\\t3.0.0')
    else:
        sys.exit(1)
elif name == 'apt-mark':
    if mode == 'hold': print('docker-ce-cli')
elif name == 'apt-cache':
    if mode != 'missing':
        candidate = '2.0.0' if mode == 'same' else '3.0.0'
        print(args[-1] + ' | ' + candidate + ' | https://download.docker.com/linux/debian trixie/stable arm64 Packages')
        print(args[-1] + ' | 4.0.0-rc | https://download.docker.com/linux/debian trixie/test arm64 Packages')
elif name == 'dpkg':
    sys.exit(subprocess.run(['/usr/bin/dpkg', *args]).returncode)
elif name == 'docker':
    if args[:2] == ['context', 'inspect']: print('unix:///var/run/docker.sock')
    elif args[0] == 'version': print('test-version')
    elif args[0] == 'ps': pass
elif name == 'apt-get':
    if mode == 'apt-fail' and '-y' in args: sys.exit(1)
'''


@unittest.skipUnless(os.geteuid() == 0, 'Script requires root')
class EngineUpdateTests(unittest.TestCase):
    def run_script(self, mode='', check=True):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            binary = task / 'bin'
            binary.mkdir()
            for name in ('apt-get', 'apt-cache', 'dpkg-query', 'dpkg', 'apt-mark', 'docker', 'systemctl'):
                p = binary / name
                p.write_text(MOCK)
                p.chmod(0o755)
            script = task / 'engine.sh'
            script.write_text((ROOT / 'update-docker-engine.sh').read_text()
                              .replace('/run/blockysetup-update.lock', str(task / 'lock'))
                              .replace('/opt/docker-engine-backups', str(task / 'backups')))
            env = dict(os.environ, PATH=str(binary) + ':' + os.environ['PATH'],
                       ENGINE_TEST_LOG=str(task / 'calls'), ENGINE_TEST_MODE=mode)
            env.pop('DOCKER_HOST', None)
            env.pop('DOCKER_CONTEXT', None)
            result = subprocess.run(['bash', str(script), *(['--check'] if check else [])],
                                    env=env, capture_output=True, text=True)
            calls = [json.loads(line) for line in (task / 'calls').read_text().splitlines()]
            backups = list((task / 'backups').glob('*/packages-before.txt')) if (task / 'backups').exists() else []
            return result, calls, bool(backups)

    def test_check_only_simulates_and_does_not_restart(self):
        result, calls, backup = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(c[0] == 'apt-get' and '--simulate' in c for c in calls))
        self.assertFalse(any(c[0] == 'apt-get' and '-y' in c for c in calls))
        self.assertFalse(any(c[:2] == ['systemctl', 'start'] for c in calls))
        self.assertFalse(backup)

    def test_update_pins_only_installed_packages_and_keeps_no_remove(self):
        result, calls, backup = self.run_script(check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        install = next(c for c in calls if c[0] == 'apt-get' and '-y' in c)
        self.assertIn('--only-upgrade', install)
        self.assertIn('--no-remove', install)
        self.assertEqual(install[-2:], ['docker-ce=3.0.0', 'docker-ce-cli=3.0.0'])
        self.assertFalse(any('upgrade' in c or 'full-upgrade' in c for c in calls))
        self.assertTrue(backup)

    def test_hold_is_not_removed_and_no_install_occurs(self):
        result, calls, _ = self.run_script('hold', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('hold', result.stderr)
        self.assertFalse(any(c[0] == 'apt-get' and 'install' in c for c in calls))
        self.assertNotIn(['apt-mark', 'unhold'], [c[:2] for c in calls])

    def test_missing_official_stable_source_aborts(self):
        result, calls, _ = self.run_script('missing', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] == 'apt-get' and 'install' in c for c in calls))

    def test_current_version_does_not_restart(self):
        result, calls, backup = self.run_script('same', check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[:2] == ['systemctl', 'start'] for c in calls))
        self.assertFalse(backup)

    def test_apt_failure_reports_backup_and_does_not_claim_success(self):
        result, calls, backup = self.run_script('apt-fail', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('nie powiodła', result.stderr)
        self.assertTrue(backup)
        self.assertFalse(any(c[:2] == ['systemctl', 'start'] for c in calls))


if __name__ == '__main__':
    unittest.main()
