#!/usr/bin/env python3
"""Update the existing Blocky stack without changing its deployment settings."""
import argparse
import copy
import datetime
import fcntl
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

SERVICES = {
    'blocky-db': ('mariadb', '/var/lib/mysql', 'blocky_db_data'),
    'blocky': ('ghcr.io/0xerr0r/blocky', '/app/cache', 'blocky_cache'),
    'blocky-prometheus': ('prom/prometheus', '/prometheus', 'blocky_prometheus_data'),
    'blocky-grafana': ('grafana/grafana', '/var/lib/grafana', 'blocky_grafana_data'),
}
RELEASES = {'blocky': '0xERR0R/blocky', 'blocky-prometheus': 'prometheus/prometheus',
            'blocky-grafana': 'grafana/grafana'}


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], check=True, **kwargs)


def inspect(kind, name):
    return json.loads(docker(kind, 'inspect', name, capture_output=True, text=True).stdout)[0]


def version(tag):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', tag)
    if not match:
        raise ValueError(f'Nie jest to stabilna wersja: {tag}')
    return tuple(map(int, match.groups()))


def get_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'BlockySetup-updater'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def database_tag(series):
    url = f'https://hub.docker.com/v2/repositories/library/mariadb/tags?page_size=100&name={series}.'
    tags = []
    for _ in range(20):
        page = get_json(url)
        tags.extend(item['name'] for item in page['results']
                    if re.fullmatch(re.escape(series) + r'\.\d+', item['name']))
        url = page.get('next')
        if not url:
            break
        if not url.startswith('https://hub.docker.com/'):
            raise ValueError('Nieoczekiwany adres metadanych MariaDB')
    else:
        raise ValueError('Zbyt wiele stron metadanych MariaDB')
    if not tags:
        raise ValueError(f'Nie znaleziono stabilnej wersji MariaDB {series}')
    return max(tags, key=version)


def validate_container(name, container):
    repo, target, volume = SERVICES[name]
    image = container['Config']['Image']
    # Older installers recreated monitoring containers by local image ID.
    managed = (container['Config'].get('Labels') or {}).get('io.blockysetup.managed') == 'true'
    if not (image.startswith(repo + ':') or image.startswith(repo + '@') or managed):
        raise ValueError(f'{name}: nieoczekiwany obraz')
    if not container['State']['Running']:
        raise ValueError(f'{name}: kontener musi działać przed aktualizacją')
    if not any(m.get('Name') == volume and m['Destination'] == target
               for m in container['Mounts']):
        raise ValueError(f'{name}: nieoczekiwany wolumen danych')
    expected_network = 'bridge' if name == 'blocky-db' else 'host'
    if container['HostConfig']['NetworkMode'] not in (expected_network, 'default' if name == 'blocky-db' else 'host'):
        raise ValueError(f'{name}: nieobsługiwany tryb sieci')
    if name == 'blocky' and not any(m['Source'] == '/opt/blocky/config.yml'
                                   and m['Destination'] == '/app/config.yml' for m in container['Mounts']):
        raise ValueError('Blocky: inny plik konfiguracji')
    if container['HostConfig'].get('AutoRemove'):
        raise ValueError(f'{name}: AutoRemove uniemożliwia zachowanie poprzedniego kontenera')


def create_payload(container, old_image, new_image):
    config = copy.deepcopy(container['Config'])
    old_defaults = old_image.get('Config') or {}
    new_defaults = new_image.get('Config') or {}
    # Adopt new image defaults, retain explicit command/entrypoint overrides.
    for key in ('Cmd', 'Entrypoint', 'WorkingDir', 'User', 'Healthcheck', 'StopSignal'):
        if config.get(key) == old_defaults.get(key):
            if key in new_defaults:
                config[key] = copy.deepcopy(new_defaults[key])
            else:
                config.pop(key, None)
    defaults = dict(value.split('=', 1) for value in new_defaults.get('Env', []) if '=' in value)
    defaults.update(value.split('=', 1) for value in config.get('Env', []) if '=' in value)
    config['Env'] = [f'{key}={value}' for key, value in defaults.items()]
    if config.get('Hostname') == container['Id'][:12]:
        config.pop('Hostname')
    config['Image'] = new_image['Id']
    config.setdefault('Labels', {})
    config['Labels'] = config['Labels'] or {}
    config['Labels']['io.blockysetup.managed'] = 'true'
    host = copy.deepcopy(container['HostConfig'])
    # Reuse effective mounts, including local lists and anonymous volumes.
    originals = {m['Target']: m for m in host.get('Mounts', [])}
    mounts = []
    for mount in container['Mounts']:
        if mount['Type'] not in ('bind', 'volume'):
            raise ValueError('Nieobsługiwane montowanie: ' + mount['Type'])
        item = copy.deepcopy(originals.get(mount['Destination'], {}))
        item.update(Type=mount['Type'], Target=mount['Destination'],
                    Source=mount.get('Name') if mount['Type'] == 'volume' else mount['Source'],
                    ReadOnly=not mount['RW'])
        if mount['Type'] == 'bind':
            item.setdefault('BindOptions', {})['Propagation'] = mount.get('Propagation') or 'rprivate'
        mounts.append(item)
    host['Binds'] = None
    host['Mounts'] = mounts
    config['HostConfig'] = host
    return config


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/var/run/docker.sock')


def create_container(name, payload, api):
    connection = UnixConnection('localhost', timeout=120)
    try:
        connection.request('POST', f'/v{api}/containers/create?name={urllib.parse.quote(name)}',
                           json.dumps(payload), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        body = json.loads(response.read())
        if response.status != 201:
            # Do not echo daemon errors: a failed create can contain secrets.
            raise RuntimeError(f'Docker create: HTTP {response.status}')
        return body['Id']
    finally:
        connection.close()


def ready(name, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = inspect('container', name)['State']
        if not state['Running']:
            raise RuntimeError(f'{name} zatrzymał się; sprawdź docker logs {name}')
        if name == 'blocky-db':
            command = ['docker', 'exec', name, 'healthcheck.sh', '--connect', '--innodb_initialized']
        elif name == 'blocky':
            command = ['docker', 'exec', name, '/app/blocky', 'healthcheck']
        else:
            port, path = (9090, '/-/ready') if name == 'blocky-prometheus' else (3001, '/api/health')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=3) as response:
                    if response.status == 200:
                        return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(2)
            continue
        if subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20).returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError(f'{name}: przekroczono czas startu; sprawdź docker logs {name}')


def main():
    parser = argparse.ArgumentParser(description='Aktualizuj cztery kontenery; nie aktualizuje systemu ani Docker Engine.')
    parser.add_argument('--check', action='store_true', help='Pokaż wybrane wersje, bez pobierania i zmian')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('Uruchom jako root')
    if os.environ.get('DOCKER_HOST') or os.environ.get('DOCKER_CONTEXT'):
        raise ValueError('Aktualizator obsługuje tylko lokalny Docker')
    context = docker('context', 'inspect', capture_output=True, text=True)
    if json.loads(context.stdout)[0]['Endpoints']['docker']['Host'] != 'unix:///var/run/docker.sock':
        raise ValueError('Aktualizator wymaga lokalnego /var/run/docker.sock')
    os.umask(0o077)
    with open('/run/blockysetup-update.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run_update(args)


def run_update(args):
    containers = {name: inspect('container', name) for name in SERVICES}
    for name, container in containers.items():
        validate_container(name, container)
    db_version = docker('exec', 'blocky-db', 'mariadbd', '--version', capture_output=True, text=True).stdout
    match = re.search(r'\b(\d+\.\d+\.\d+)(?:-MariaDB|\b)', db_version)
    if not match:
        raise ValueError('Nie można ustalić wersji MariaDB')
    current_db = match.group(1)
    series = '.'.join(current_db.split('.')[:2])
    targets = {'blocky-db': 'mariadb:' + database_tag(series)}
    for name, repo in RELEASES.items():
        release = get_json(f'https://api.github.com/repos/{repo}/releases/latest')
        tag = release['tag_name']
        version(tag)
        if release.get('prerelease') or release.get('draft'):
            raise ValueError('Wydanie testowe: ' + repo)
        if name == 'blocky-grafana':
            tag = tag.removeprefix('v')
        targets[name] = SERVICES[name][0] + ':' + tag
    old_images = {name: inspect('image', item['Image']) for name, item in containers.items()}
    for name, target in targets.items():
        candidates = [containers[name]['Config']['Image'], *(old_images[name].get('RepoTags') or [])]
        if name == 'blocky-db':
            old_versions = [version(current_db)]
        else:
            old_versions = [version(item.rsplit(':', 1)[1]) for item in candidates
                            if item.startswith(SERVICES[name][0] + ':')
                            and re.fullmatch(r'v?\d+\.\d+\.\d+', item.rsplit(':', 1)[1])]
        if old_versions and version(target.rsplit(':', 1)[1]) < max(old_versions):
            raise ValueError(f'{name}: odmowa obniżenia wersji')
        print(f'{name}: {target}', flush=True)
    if args.check:
        return
    for target in targets.values():
        docker('pull', target)
    new_images = {name: inspect('image', target) for name, target in targets.items()}
    changed = [name for name in SERVICES if containers[name]['Image'] != new_images[name]['Id']]
    if not changed:
        print('Wszystkie obrazy są aktualne. Nie restartowano usług.')
        return
    payloads = {name: create_payload(containers[name], old_images[name], new_images[name]) for name in changed}
    # Validate against precisely the current mounts, including /root/blocky.
    docker('run', '--rm', '--network', 'host', '--volumes-from', 'blocky:ro',
           '--entrypoint', '/app/blocky', new_images['blocky']['Id'],
           'validate', '--config', '/app/config.yml')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = Path('/opt/blocky-backups') / stamp
    backup.mkdir(parents=True, mode=0o700)
    shutil.copytree('/opt/blocky', backup / 'config')
    for i, mount in enumerate(containers['blocky']['Mounts']):
        if mount['Type'] == 'bind' and mount['Source'] != '/opt/blocky/config.yml':
            source = Path(mount['Source'])
            destination = backup / f'blocky-bind-{i}'
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
    (backup / 'containers.json').write_text(json.dumps(containers, indent=2))
    (backup / 'plan.json').write_text(json.dumps(targets, indent=2))
    print(f'Kopia: {backup}', flush=True)
    with (backup / 'mariadb.sql').open('wb') as output:
        docker('exec', 'blocky-db', 'sh', '-c',
               'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb-dump -u root '
               '--all-databases --single-transaction --routines --events --triggers', stdout=output)
    if not (backup / 'mariadb.sql').stat().st_size:
        raise ValueError('Pusta kopia bazy; aktualizacja przerwana')
    api = docker('version', '--format', '{{.Server.APIVersion}}', capture_output=True, text=True).stdout.strip()
    for name in changed:
        previous = f'{name}-before-{stamp}'
        print(f'Aktualizacja {name}...', flush=True)
        docker('stop', '--time', '60', name)
        # These copies are made while the writers are stopped; safe for migration rollback.
        try:
            if name in ('blocky-db', 'blocky-grafana'):
                docker('cp', f'{name}:{SERVICES[name][1]}', str(backup / (name + '-data')))
        except BaseException:
            docker('start', name)
            raise
        docker('rename', name, previous)
        try:
            created = create_container(name, payloads[name], api)
        except BaseException:
            docker('rename', previous, name)
            docker('start', name)
            raise
        try:
            docker('start', created)
            ready(name)
        except BaseException:
            # A new DB/Grafana may have migrated its volume: never blindly start an old image.
            print(f'Przerwano. Poprzedni kontener: {previous}. Kopia: {backup}. '
                  f'Nie uruchamiaj starszej wersji na zmigrowanych danych.', file=sys.stderr)
            raise
        docker('update', '--restart=no', previous)
    print('Gotowe. Sprawdź historię i wykresy w Grafanie. Stare kontenery i kopie zachowano.')


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        # CLI exception text can contain credentials returned by the daemon; suppress it.
        if isinstance(error, (ValueError, RuntimeError)):
            print(str(error), file=sys.stderr)
        else:
            print(f'Aktualizacja przerwana ({type(error).__name__}); usług dalej nie zmieniano.', file=sys.stderr)
        sys.exit(1)
