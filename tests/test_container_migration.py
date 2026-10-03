import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def run_guard(self, image="mariadb:11.4", volume="blocky_db_data", password="original", labels=None):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "mariadb.env").write_text("MARIADB_PASSWORD=original\n")
            container = {"Config": {"Image": image, "Labels": labels,
                                      "Env": ["MARIADB_PASSWORD=" + password]},
                         "Mounts": [{"Type": "volume", "Name": volume, "Destination": "/var/lib/mysql"}]}
            (folder / "container.json").write_text(json.dumps([container]))
            docker = folder / "docker"
            docker.write_text("#!/bin/sh\n[ \"$2\" = blocky-db ] || exit 1\ncat \"$FIXTURE\"\n")
            docker.chmod(0o755)
            env = dict(os.environ, PATH=str(folder) + ":" + os.environ["PATH"],
                       FIXTURE=str(folder / "container.json"))
            return subprocess.run(["python3", str(ROOT / "scripts/check-containers.py"), str(folder)],
                                  env=env, capture_output=True, text=True)

    def test_adopts_previous_manual_database(self):
        self.assertEqual(self.run_guard().returncode, 0)

    def test_rejects_unrelated_volume(self):
        self.assertNotEqual(self.run_guard(volume="another_database").returncode, 0)

    def test_rejects_wrong_credentials_without_printing_password(self):
        result = self.run_guard(password="private-secret")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("private-secret", result.stderr)
        self.assertNotIn("original", result.stderr)

    def test_rerun_accepts_installer_image_id(self):
        self.assertEqual(self.run_guard(image="sha256:abc", labels={"io.blockysetup.managed": "true"}).returncode, 0)
