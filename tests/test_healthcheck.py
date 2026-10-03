import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HealthcheckTests(unittest.TestCase):
    def test_healthcheck_executes_binary_in_scratch_container(self):
        script = (ROOT / "install.sh").read_text()
        start = script.index("ready=false", script.index("backup=$("))
        end = script.index("\nif ! $ready", start)
        check = script[start:end] + "\n$ready\n"
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            docker = folder / "docker"
            # Model docker exec in the official scratch image: ENTRYPOINT is
            # not prepended and the only executable is /app/blocky.
            docker.write_text('#!/bin/sh\n[ "$1" = exec ] && [ "$2" = blocky ] && '
                              '[ "$3" = /app/blocky ] && [ "$4" = healthcheck ] || exit 127\n')
            docker.chmod(0o755)
            sleep = folder / "sleep"
            sleep.write_text("#!/bin/sh\nexit 0\n")
            sleep.chmod(0o755)
            env = dict(os.environ, PATH=str(folder) + ":" + os.environ["PATH"])
            result = subprocess.run(["bash", "-c", check], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
