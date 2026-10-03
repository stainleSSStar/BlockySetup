from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GrafanaPermissionTests(unittest.TestCase):
    def test_provisioning_readable_with_restrictive_root_umask(self):
        script = (ROOT / "install.sh").read_text()
        start = script.index('mkdir -p "$monitoring/grafana"')
        end = script.index("\nlog_opts=", start)
        with tempfile.TemporaryDirectory() as tmp:
            monitoring = Path(tmp)
            secret = monitoring / "grafana.env"
            secret.write_text("GF_SECURITY_ADMIN_PASSWORD=private\n")
            secret.chmod(0o600)
            result = subprocess.run(["bash", "-c", "set -eu; umask 077; "
                                     'repo=$1; monitoring=$2;\n' + script[start:end],
                                     "test", str(ROOT), str(monitoring)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for path in (monitoring / "grafana").rglob("*"):
                self.assertEqual(path.stat().st_mode & 0o777, 0o755 if path.is_dir() else 0o644)
            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
