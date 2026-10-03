import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("configure", ROOT / "scripts/configure.py")
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


class ConfigurationTests(unittest.TestCase):
    def test_preserves_dns_settings_and_file_inode_and_is_idempotent(self):
        original = (ROOT / "config.yml").read_text()
        before = yaml.safe_load(original)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yml"
            path.write_text(original)
            inode = path.stat().st_ino
            backup = configure.configure(path, "abc123")
            self.assertEqual(backup.read_text(), original)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_ino, inode)
            after = yaml.safe_load(path.read_text())
            self.assertEqual(after["upstreams"], before["upstreams"])
            self.assertEqual(after["blocking"]["denylists"], before["blocking"]["denylists"])
            self.assertEqual(after["blocking"]["clientGroupsBlock"], before["blocking"]["clientGroupsBlock"])
            self.assertEqual(after["dnssec"], before["dnssec"])
            self.assertIn("loc=UTC", after["queryLog"]["target"])
            self.assertIsNone(configure.configure(path, "abc123"))
            self.assertEqual(len(list(Path(tmp).glob("*.backup-*"))), 1)

    def test_replaces_existing_querylog_instead_of_adding_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yml"
            path.write_text((ROOT / "config.yml").read_text() + "\nqueryLog:\n  type: console\n")
            configure.configure(path, "abc123")
            self.assertEqual(path.read_text().count("\nqueryLog:"), 1)
            self.assertEqual(yaml.safe_load(path.read_text())["queryLog"]["type"], "mysql")

    def test_duplicate_yaml_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yml"
            original = "ports: {dns: 53, http: 4000}\nqueryLog: {}\nqueryLog: {}\n"
            path.write_text(original)
            with self.assertRaises(ValueError):
                configure.configure(path, "abc123")
            self.assertEqual(path.read_text(), original)

    def test_existing_credentials_and_grafana_password_survive_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            password = configure.credentials(folder)
            first_grafana = configure.read_env(folder / "grafana.env")
            self.assertEqual(configure.credentials(folder), password)
            self.assertEqual(configure.read_env(folder / "grafana.env"), first_grafana)
            self.assertEqual((folder / "mariadb.env").stat().st_mode & 0o777, 0o600)

    def test_adopts_credentials_from_previous_manual_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "mariadb.env").write_text("MARIADB_DATABASE=blocky\nMARIADB_USER=blocky\nMARIADB_PASSWORD=old-secret\nMARIADB_RANDOM_ROOT_PASSWORD=1\n")
            self.assertEqual(configure.credentials(folder), "old-secret")
            self.assertIn("MARIADB_RANDOM_ROOT_PASSWORD=1", (folder / "mariadb.env").read_text())

    def test_dashboard_datasources_are_provisioned(self):
        provision = yaml.safe_load((ROOT / "monitoring/grafana/provisioning/datasources/blocky.yml").read_text())
        uids = {source["uid"] for source in provision["datasources"]}
        for path in (ROOT / "monitoring/grafana/dashboards").glob("*.json"):
            dashboard = json.loads(path.read_text())
            self.assertTrue(dashboard["panels"])
            for panel in dashboard["panels"]:
                self.assertIn(panel["datasource"]["uid"], uids)
                if panel["datasource"]["type"] == "mysql":
                    query = panel["targets"][0]["rawSql"]
                    self.assertIn("log_entries", query)
                    self.assertIn("$__timeFilter(request_ts)", query)


if __name__ == "__main__":
    unittest.main()
