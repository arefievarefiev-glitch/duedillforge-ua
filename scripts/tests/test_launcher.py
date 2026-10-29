"""The launcher scripts/ddf runs the program as the plugin runs it (script path, not -m)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "POSIX launcher")
class Launcher(unittest.TestCase):
    def run_ddf(self, *args, data=None):
        env = dict(os.environ, CLAUDE_PLUGIN_DATA=data)
        return subprocess.run(["bash", str(ROOT / "scripts" / "ddf"), *args], capture_output=True, text=True, env=env, timeout=60)

    def test_deps_runs_through_launcher(self):
        with tempfile.TemporaryDirectory() as data:
            proc = self.run_ddf("deps", data=data)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("python", json.loads(proc.stdout))

    def test_bad_brief_is_reported_not_crashed(self):
        with tempfile.TemporaryDirectory() as data:
            brief = Path(data) / "b.json"
            brief.write_text(json.dumps({"object": "", "blocks": []}), encoding="utf-8")
            proc = self.run_ddf("task", "new", "--brief", str(brief), data=data)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["error"], "bad_brief")

    def test_no_task_message(self):
        with tempfile.TemporaryDirectory() as data:
            proc = self.run_ddf("next", data=data)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("no_task", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
