#!/usr/bin/env python3
"""Do not replace unrelated containers or attach an unknown database volume."""
import json
import subprocess
import sys

specs = {
    "blocky-db": ("mariadb:", "blocky_db_data", "/var/lib/mysql"),
    "blocky-prometheus": ("prom/prometheus:", "blocky_prometheus_data", "/prometheus"),
    "blocky-grafana": ("grafana/grafana:", "blocky_grafana_data", "/var/lib/grafana"),
}
for name, (image, volume, target) in specs.items():
    proc = subprocess.run(["docker", "inspect", name], capture_output=True, text=True)
    if proc.returncode:
        continue
    container = json.loads(proc.stdout)[0]
    mounts = container.get("Mounts", [])
    managed = (container["Config"].get("Labels") or {}).get("io.blockysetup.managed") == "true"
    if not (container["Config"]["Image"].startswith(image) or managed) or not any(
        mount.get("Type") == "volume" and mount.get("Name") == volume
        and mount.get("Destination") == target for mount in mounts
    ):
        sys.exit(f"STOP: kontener {name} ma inną konfigurację; nie będzie usuwany.")
    if name == "blocky-db" and not any(value.startswith("MARIADB_PASSWORD=")
                                       for value in container["Config"].get("Env", [])):
        sys.exit("STOP: nie można potwierdzić hasła istniejącej bazy.")
    if name == "blocky-db":
        from configure import read_env
        from pathlib import Path
        expected = read_env(Path(sys.argv[1]) / "mariadb.env")["MARIADB_PASSWORD"]
        actual = dict(value.split("=", 1) for value in container["Config"].get("Env", []) if "=" in value)
        if actual.get("MARIADB_PASSWORD") != expected:
            sys.exit("STOP: hasło istniejącej bazy nie zgadza się z mariadb.env.")
