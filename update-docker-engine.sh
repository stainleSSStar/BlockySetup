#!/usr/bin/env bash
# Upgrade the installed official Docker packages; never run a system-wide upgrade.
set -euo pipefail
export LC_ALL=C
umask 077

case "${1:-}" in
    '') check=false ;;
    --check) check=true ;;
    *) echo 'Użycie: bash update-docker-engine.sh [--check]' >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || exit 2
[[ $EUID -eq 0 ]] || { echo 'Uruchom jako root.' >&2; exit 1; }
for command in apt-get apt-cache dpkg-query dpkg apt-mark docker systemctl flock python3 timeout; do
    command -v "$command" >/dev/null || { echo "Brak programu: $command" >&2; exit 1; }
done
[[ -z ${DOCKER_HOST:-} && -z ${DOCKER_CONTEXT:-} ]] || { echo 'Wymagany lokalny Docker.' >&2; exit 1; }
endpoint=$(docker context inspect --format '{{.Endpoints.docker.Host}}')
[[ $endpoint == unix:///var/run/docker.sock ]] || { echo 'Wymagany lokalny /var/run/docker.sock.' >&2; exit 1; }
# Shared with update.sh: do not upgrade the daemon during a stack update.
exec 9>/run/blockysetup-update.lock
flock -n 9 || { echo 'Inna aktualizacja BlockySetup już działa.' >&2; exit 1; }

installed_version() {
    local status
    status=$(dpkg-query -W -f='${db:Status-Status}\t${Version}' "$1" 2>/dev/null) || return 1
    [[ $status == installed$'\t'* ]] || return 1
    printf '%s\n' "${status#*$'\t'}"
}
installed_version docker-ce >/dev/null || {
    echo 'Skrypt wymaga Docker CE z oficjalnego repozytorium APT; nie migruje docker.io.' >&2
    exit 1
}
systemctl is-active --quiet docker || { echo 'Docker musi działać przed aktualizacją.' >&2; exit 1; }
docker info >/dev/null

apt-get -o APT::Update::Error-Mode=any update
packages=()
targets=()
versions=()
held=$(apt-mark showhold)
for package in docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin docker-ce-rootless-extras; do
    current=$(installed_version "$package") || continue
    # Select only versions published in Docker's official stable Debian channel.
    candidate=$(apt-cache madison "$package" | awk -F '|' '
        !found && $3 ~ /https:\/\/download[.]docker[.]com\/linux\/debian/ && $3 ~ /\/stable[[:space:]]/ {
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; found=1
        }')
    [[ -n $candidate ]] || { echo "Brak $package w oficjalnym kanale stable. Przerwano." >&2; exit 1; }
    packages+=("$package")
    versions+=("$package=$current")
    echo "$package: $current -> $candidate"
    if dpkg --compare-versions "$candidate" gt "$current"; then
        if printf '%s\n' "$held" | grep -Fxq "$package"; then
            echo "$package ma blokadę APT hold; skrypt jej nie usuwa." >&2
            exit 1
        fi
        targets+=("$package=$candidate")
    fi
done
if [[ ${#targets[@]} -eq 0 ]]; then
    echo 'Zainstalowane pakiety Dockera są aktualne. Nie restartowano usług.'
    exit 0
fi
apt-get --simulate --no-remove --no-install-recommends install --only-upgrade "${targets[@]}"
$check && exit 0

backup="/opt/docker-engine-backups/$(date -u +%Y%m%dT%H%M%S)-$$"
mkdir -p -m 700 "$backup"
printf '%s\n' "${versions[@]}" > "$backup/packages-before.txt"
docker version > "$backup/docker-version-before.txt"
docker ps -a --format '{{.Names}}\t{{.Status}}' > "$backup/containers-before.txt"
docker ps --format '{{.Names}}' > "$backup/running-before.txt"
for source in /etc/docker /etc/default/docker /etc/systemd/system/docker.service.d /etc/systemd/system/containerd.service.d; do
    [[ -e $source ]] || continue
    cp -a --parents "$source" "$backup/"
done
systemctl cat docker > "$backup/docker-service-before.txt"
echo "Kopia konfiguracji i wersji pakietów: $backup"
echo 'APT może zrestartować Docker i na krótko przerwać DNS oraz monitoring.'

if ! apt-get -y --no-remove --no-install-recommends install --only-upgrade "${targets[@]}"; then
    echo "Aktualizacja APT nie powiodła się. Sprawdź stan pakietów. Kopia: $backup" >&2
    exit 1
fi
systemctl start docker
ready=false
for ((attempt=0; attempt<60; attempt++)); do
    if timeout 5 docker info >/dev/null 2>&1; then ready=true; break; fi
    sleep 2
done
$ready || { echo "Docker nie odpowiada. Sprawdź journalctl -u docker. Kopia: $backup" >&2; exit 1; }

# Restart only the four known services if they were running before the upgrade.
for name in blocky-db blocky blocky-prometheus blocky-grafana; do
    grep -Fxq "$name" "$backup/running-before.txt" || continue
    running=$(docker inspect --format '{{.State.Running}}' "$name")
    [[ $running == true ]] || docker start "$name" >/dev/null
    python3 - "$name" "$(dirname "$(readlink -f "$0")")/scripts" <<'PY'
import sys
sys.path.insert(0, sys.argv[2])
from update import ready
ready(sys.argv[1])
PY
done
docker version > "$backup/docker-version-after.txt"
dpkg-query -W -f='${Package}\t${Version}\n' "${packages[@]}" > "$backup/packages-after.txt"
docker ps --format '{{.Names}}' > "$backup/running-after.txt"
missing=false
while IFS= read -r name; do
    if ! grep -Fxq "$name" "$backup/running-after.txt"; then
        echo "Kontener nie wrócił do działania: $name" >&2
        missing=true
    fi
done < "$backup/running-before.txt"
$missing && exit 1
echo 'Docker Engine zaktualizowany. Kontenery wcześniej uruchomione działają.'
docker version --format 'Engine: {{.Server.Version}} | CLI: {{.Client.Version}}'
