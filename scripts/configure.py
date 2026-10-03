#!/usr/bin/env python3
"""Prepare credentials and update Blocky's existing configuration without duplicate keys."""
import argparse
import datetime
import os
from pathlib import Path
import secrets
import sys
import yaml


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError(f"Powtórzony klucz YAML: {key}")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def read_env(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if line and not line.startswith("#"))


def write_env(path, values):
    # Credentials never go into the git checkout or stdout.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in values.items()))


def credentials(directory):
    directory.mkdir(parents=True, exist_ok=True)
    db_path = directory / "mariadb.env"
    if db_path.exists():
        db = read_env(db_path)
        if db.get("MARIADB_DATABASE") != "blocky" or db.get("MARIADB_USER") != "blocky":
            raise ValueError("Istniejący mariadb.env musi wskazywać bazę i użytkownika blocky")
        password = db.get("MARIADB_PASSWORD", "")
        if not password or any(char in password for char in "'\r\n"):
            raise ValueError("Nieprawidłowe hasło w mariadb.env")
        os.chmod(db_path, 0o600)
    else:
        password = secrets.token_hex(24)
        write_env(db_path, {"MARIADB_DATABASE": "blocky", "MARIADB_USER": "blocky",
                           "MARIADB_PASSWORD": password,
                           "MARIADB_ROOT_PASSWORD": secrets.token_hex(32)})
    grafana_path = directory / "grafana.env"
    grafana = read_env(grafana_path) if grafana_path.exists() else {}
    grafana.setdefault("GF_SECURITY_ADMIN_PASSWORD", secrets.token_hex(16))
    grafana["BLOCKY_DB_PASSWORD"] = password
    write_env(grafana_path, grafana)
    return password


def configure(path, password):
    original = path.read_text()
    config = yaml.load(original, Loader=UniqueLoader)
    if not isinstance(config, dict):
        raise ValueError("Konfiguracja Blocky musi być mapą YAML")
    if config.get("ports", {}).get("dns") != 53 or config.get("ports", {}).get("http") != 4000:
        raise ValueError("Instalator wymaga ports.dns: 53 i ports.http: 4000")
    before = yaml.load(original, Loader=UniqueLoader)
    config.setdefault("prometheus", {}).update(enable=True, path="/metrics")
    downloads = config.setdefault("blocking", {}).setdefault("loading", {}).setdefault("downloads", {})
    downloads["cachePath"] = "/app/cache/lists"
    config["queryLog"] = {
        "type": "mysql",
        "target": f"blocky:{password}@tcp(127.0.0.1:3307)/blocky?charset=utf8mb4&parseTime=True&loc=UTC&timeout=15s",
        "logRetentionDays": 7, "flushInterval": "10s",
        "creationAttempts": 10, "creationCooldown": "3s",
    }
    if config == before:
        return None
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.backup-{stamp}")
    backup.write_text(original)
    os.chmod(backup, 0o600)
    # Keep the inode: running Docker containers bind-mount this single file.
    path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True))
    return backup


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["credentials", "config"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    try:
        password = credentials(args.directory)
        if args.mode == "config":
            backup = configure(args.config, password)
            print(backup or "")
    except (OSError, ValueError, yaml.YAMLError) as error:
        # YAML parser errors may contain a DSN/password; do not print their excerpts.
        print("Błąd przygotowania konfiguracji: " + (str(error) if isinstance(error, ValueError)
              and not isinstance(error, yaml.YAMLError) else type(error).__name__), file=sys.stderr)
        sys.exit(1)
