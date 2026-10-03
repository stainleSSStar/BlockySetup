#!/usr/bin/env bash
set -euo pipefail

[[ $EUID == 0 ]] || { echo 'Uruchom jako root.' >&2; exit 1; }
mode=${1:-all}
[[ $mode == all || $mode == --monitoring-only ]] || { echo 'Użycie: bash install.sh [--monitoring-only]'; exit 1; }
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
base=/opt/blocky
monitoring=$base/monitoring
if ! command -v docker >/dev/null; then
    command -v dietpi-software >/dev/null || { echo 'Zainstaluj Docker Engine przed instalacją.'; exit 1; }
    dietpi-software install 162
fi
if ! python3 -c 'import yaml' >/dev/null 2>&1 || ! command -v curl >/dev/null; then
    apt-get update
    apt-get install -y python3 python3-yaml curl ca-certificates
fi
systemctl enable --now docker
docker info >/dev/null
mkdir -p "$monitoring"
if [[ ! -f $base/config.yml ]]; then
    [[ $mode == all ]] || { echo 'Brak /opt/blocky/config.yml'; exit 1; }
    install -m 0644 "$repo/config.yml" "$base/config.yml"
fi
if docker container inspect blocky >/dev/null 2>&1; then
    docker inspect blocky | python3 -c '
import json,sys
c=json.load(sys.stdin)[0]
if c["HostConfig"]["NetworkMode"] != "host" or not any(
    m.get("Source")=="/opt/blocky/config.yml" and m.get("Destination")=="/app/config.yml" for m in c["Mounts"]):
    sys.exit("Istniejący blocky musi używać host network i /opt/blocky/config.yml")
if not any(m.get("Name")=="blocky_cache" and m.get("Destination")=="/app/cache" for m in c["Mounts"]):
    sys.exit("Istniejący blocky musi używać wolumenu blocky_cache w /app/cache")
if c["Config"]["Image"] not in ("ghcr.io/0xerr0r/blocky:v0.35.0", "spx01/blocky:v0.35.0"):
    sys.exit("Instalator obsługuje istniejący kontener Blocky v0.35.0")'
else
    [[ $mode == all ]] || { echo 'Brak kontenera blocky'; exit 1; }
    if ss -H -lntup '( sport = :53 or sport = :4000 )' | grep -q .; then
        echo 'Port 53 lub 4000 zajęty. Rozwiąż konflikt przed instalacją.'; exit 1
    fi
fi
python3 "$repo/scripts/configure.py" credentials --directory "$monitoring"
python3 "$repo/scripts/check-containers.py" "$monitoring"
for item in blocky-db:3307 blocky-prometheus:9090 blocky-grafana:3001; do
    name=${item%:*}; port=${item#*:}
    if ! docker inspect "$name" >/dev/null 2>&1 && ss -H -lntup "( sport = :$port )" | grep -q .; then
        echo "Port $port zajęty przez inną usługę. Rozwiąż konflikt przed instalacją."; exit 1
    fi
done
if docker volume inspect blocky_db_data >/dev/null 2>&1 && ! docker inspect blocky-db >/dev/null 2>&1; then
    echo 'Istnieje wolumen bazy bez kontenera blocky-db. Najpierw odtwórz kontener z jego oryginalnym hasłem.'; exit 1
fi

# Retain existing monitoring images; fetch images for new services before replacement.
db_image=mariadb:11.4.13
prom_image=prom/prometheus:v3.5.0
grafana_image=grafana/grafana:12.1.1
if docker inspect blocky-db >/dev/null 2>&1; then db_image=$(docker inspect --format '{{.Image}}' blocky-db); fi
if docker inspect blocky-prometheus >/dev/null 2>&1; then prom_image=$(docker inspect --format '{{.Image}}' blocky-prometheus); fi
if docker inspect blocky-grafana >/dev/null 2>&1; then grafana_image=$(docker inspect --format '{{.Image}}' blocky-grafana); fi
for image in ghcr.io/0xerr0r/blocky:v0.35.0 "$prom_image" "$grafana_image" "$db_image"; do
    [[ $image == sha256:* ]] && continue
    docker pull "$image"
done
install -m 0644 "$repo/monitoring/prometheus.yml" "$monitoring/prometheus.yml"
mkdir -p "$monitoring/grafana"
cp -R "$repo/monitoring/grafana/provisioning" "$repo/monitoring/grafana/dashboards" "$monitoring/grafana/"
log_opts=(--log-driver json-file --log-opt max-size=10m --log-opt max-file=3)
replace() {
    if docker inspect "$1" >/dev/null 2>&1; then
        docker stop "$1" >/dev/null
        docker rm "$1" >/dev/null
    fi
}
replace blocky-db
docker run -d --name blocky-db --label io.blockysetup.managed=true --restart unless-stopped "${log_opts[@]}" \
    -p 127.0.0.1:3307:3306 --env-file "$monitoring/mariadb.env" \
    --mount type=volume,src=blocky_db_data,dst=/var/lib/mysql "$db_image"
ready=false
for ((attempt=0; attempt<60; attempt++)); do
    if docker exec blocky-db healthcheck.sh --connect --innodb_initialized >/dev/null 2>&1; then ready=true; break; fi
    sleep 2
done
$ready || { echo 'MariaDB nie jest gotowa. Sprawdź docker logs blocky-db'; exit 1; }
docker exec blocky-db sh -c 'MYSQL_PWD="$MARIADB_PASSWORD" mariadb --user=blocky --database=blocky -e "SELECT 1"' >/dev/null

backup=$(python3 "$repo/scripts/configure.py" config --directory "$monitoring" --config "$base/config.yml")
restore_config() {
    if [[ -n $backup ]]; then cat "$backup" > "$base/config.yml"; fi
    echo 'Przywrócono wcześniejszą konfigurację Blocky.' >&2
}
if ! docker run --rm --network host --mount type=bind,src="$base/config.yml",dst=/app/config.yml,readonly \
    ghcr.io/0xerr0r/blocky:v0.35.0 validate --config /app/config.yml; then
    restore_config; exit 1
fi
if docker inspect blocky >/dev/null 2>&1; then
    if [[ -n $backup ]]; then docker restart blocky >/dev/null; else docker start blocky >/dev/null; fi
else
    docker run -d --name blocky --restart unless-stopped --network host --cap-add NET_BIND_SERVICE \
        "${log_opts[@]}" -e TZ=Europe/Warsaw \
        --mount type=bind,src="$base/config.yml",dst=/app/config.yml,readonly \
        --mount type=volume,src=blocky_cache,dst=/app/cache ghcr.io/0xerr0r/blocky:v0.35.0
fi
ready=false
for ((attempt=0; attempt<60; attempt++)); do
    if docker exec blocky /app/blocky healthcheck >/dev/null 2>&1; then ready=true; break; fi
    sleep 2
done
if ! $ready; then
    echo 'Blocky nie przeszedł healthcheck. Sprawdź: docker logs --tail 100 blocky' >&2
    restore_config; docker restart blocky >/dev/null; exit 1
fi
if ! docker exec blocky-db sh -c 'MYSQL_PWD="$MARIADB_PASSWORD" mariadb --user=blocky --database=blocky -e "SELECT 1 FROM log_entries LIMIT 0"' >/dev/null; then
    restore_config; docker restart blocky >/dev/null; exit 1
fi

replace blocky-prometheus
docker run -d --name blocky-prometheus --label io.blockysetup.managed=true --restart unless-stopped --network host "${log_opts[@]}" \
    --mount type=bind,src="$monitoring/prometheus.yml",dst=/etc/prometheus/prometheus.yml,readonly \
    --mount type=volume,src=blocky_prometheus_data,dst=/prometheus "$prom_image" \
    --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/prometheus \
    --storage.tsdb.retention.time=15d --storage.tsdb.retention.size=2GB --web.listen-address=127.0.0.1:9090
replace blocky-grafana
docker run -d --name blocky-grafana --label io.blockysetup.managed=true --restart unless-stopped --network host "${log_opts[@]}" \
    --env-file "$monitoring/grafana.env" -e GF_SERVER_HTTP_PORT=3001 -e GF_USERS_ALLOW_SIGN_UP=false \
    --mount type=volume,src=blocky_grafana_data,dst=/var/lib/grafana \
    --mount type=bind,src="$monitoring/grafana/provisioning",dst=/etc/grafana/provisioning,readonly \
    --mount type=bind,src="$monitoring/grafana/dashboards",dst=/var/lib/grafana/dashboards,readonly \
    "$grafana_image"
curl --retry 30 --retry-delay 2 --retry-connrefused -fsS http://127.0.0.1:9090/-/ready >/dev/null
curl --retry 30 --retry-delay 2 --retry-connrefused -fsS http://127.0.0.1:3001/api/health >/dev/null
echo 'Gotowe. Grafana: http://IP_RPI:3001 — login admin.'
echo 'Nowa instalacja: hasło w /opt/blocky/monitoring/grafana.env (GF_SECURITY_ADMIN_PASSWORD).'
echo 'Istniejąca Grafana: zachowano dotychczasowe hasło i dane.'
echo 'Dashboards → Blocky: statystyki oraz historia DNS. Historia zaczyna się od włączenia queryLog.'
