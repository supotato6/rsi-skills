"""Consumer contracts exercised against a real, movable whole-library snapshot."""
import json
from pathlib import Path
import runpy
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rsi consumer ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshot = self.root / "skills"
        subprocess.run([sys.executable, str(ROOT / "scripts/skills.py"), "install",
                        "--target", str(self.snapshot)], check=True, capture_output=True)

    def runtime(self, folder=None):
        folder = folder or self.snapshot
        return runpy.run_path(str(folder / ".rsi-runtime.py"))["Snapshot"](folder)

    def test_movable_snapshot_keeps_references_and_helpers(self):
        moved = self.root / "moved"
        shutil.copytree(self.snapshot, moved)
        shutil.rmtree(self.snapshot)
        runtime = self.runtime(moved)
        self.assertIn("rsi-kaggle-optimize", {s["name"] for s in runtime.entries()})
        self.assertTrue((moved / "rsi-kaggle-open-sota/scripts/discover.py").is_file())
        self.assertTrue((moved / "rsi-kaggle-optimize/references/regression-contract.md").is_file())
        for entry in runtime.entries():
            self.assertTrue(Path(entry["path"]).is_relative_to(moved))

    def test_discovery_is_lazy_and_reads_record_actual_resources(self):
        runtime = self.runtime()
        events = self.root / "events.jsonl"
        runtime.entries()
        self.assertFalse(events.exists())
        body = runtime.read("rsi-kaggle-optimize", events=events, scope="research")
        self.assertIn("优化", body)
        resource = runtime.read("rsi-kaggle-optimize", resource="references/regression-contract.md",
                                events=events, scope="research")
        self.assertIn("candidate", resource)
        records = [json.loads(line) for line in events.read_text().splitlines()]
        self.assertEqual([e["event"] for e in records], ["skill.loaded", "skill.resource_loaded"])
        self.assertEqual(records[0]["sha256"], next(
            s["sha256"] for s in runtime.entries() if s["name"] == "rsi-kaggle-optimize"))
        self.assertEqual(records[1]["resource"], "references/regression-contract.md")
        with self.assertRaises(ValueError):
            runtime.read("rsi-kaggle-optimize", resource="../../.rsi-skills.json", events=events)
        self.assertEqual(len(events.read_text().splitlines()), 2)

    def test_workflow_stages_whole_library_and_expands_only_composite(self):
        staged = self.runtime().stage(self.root / "trial")
        events = self.root / "trial/events.jsonl"
        prompt = staged.render(["rsi-solve-tabular-task"], events=events, scope="solver")
        self.assertIn("rsi-validation-design", prompt)
        records = [json.loads(line) for line in events.read_text().splitlines()]
        names = {e["skill"] for e in records}
        self.assertIn("rsi-solve-tabular-task", names)
        self.assertIn("rsi-artifact-check", names)
        self.assertNotIn("rsi-kaggle-optimize", names)
        self.assertEqual(len(staged.entries()), len(self.runtime().entries()))
        self.runtime().stage(self.root / "trial")

    def test_auto_discovery_exposes_every_skill_but_only_selected_reads_emit_events(self):
        runtime = self.runtime()
        events = self.root / "events with spaces.jsonl"
        prompt = runtime.discover(events=events, scope="optimizer")
        for entry in runtime.entries():
            self.assertIn(entry["name"], prompt)
            self.assertIn(entry["description"], prompt)
        self.assertNotIn("Skill rsi-", prompt)
        self.assertFalse(events.exists())
        command = shlex.split(prompt.split("Read a selected skill: ")[1].splitlines()[0])
        command[command.index("SKILL_NAME")] = "rsi-kaggle-optimize"
        subprocess.run(command, check=True, capture_output=True)
        event = json.loads(events.read_text())
        self.assertEqual(event["skill"], "rsi-kaggle-optimize")
        self.assertEqual(event["scope"], "optimizer")

    def test_discovery_commands_survive_workspace_mount_or_move(self):
        workspace = self.root / 'host-workspace'
        staged = self.runtime().stage(workspace)
        prompt = staged.discover(events=workspace / 'events.jsonl', scope='solver', relative_to=workspace)
        self.assertNotIn(str(workspace), prompt)
        command = shlex.split(prompt.split('Read a selected skill: ')[1].splitlines()[0])
        command[command.index('SKILL_NAME')] = 'rsi-error-analysis'
        moved = self.root / 'container-workspace'
        workspace.rename(moved)
        result = subprocess.run(command, cwd=moved, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((moved / 'events.jsonl').read_text())['skill'], 'rsi-error-analysis')

    def test_changed_snapshot_and_runtime_are_rejected(self):
        runtime = self.runtime()
        (self.snapshot / "rsi-kaggle-optimize/references/regression-contract.md").write_text("changed")
        with self.assertRaises(ValueError):
            runtime.read("rsi-kaggle-optimize")
        with self.assertRaises(ValueError):
            self.runtime()

    def test_changed_helper_blocks_installer_updates(self):
        (self.snapshot / ".rsi-runtime.py").write_text("changed")
        result = subprocess.run([sys.executable, str(ROOT / "scripts/skills.py"), "install",
                                 "--target", str(self.snapshot), "--update"], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_changed_runtime_blocks_reads_from_existing_snapshot(self):
        runtime = self.runtime()
        helper = self.snapshot / ".rsi-runtime.py"
        helper.write_text(helper.read_text() + "\n# changed\n")
        with self.assertRaises(ValueError):
            runtime.read("rsi-using-skills")

    def test_runtime_cli_is_usable_without_source_imports(self):
        events = self.root / "cli-events.jsonl"
        result = subprocess.run([sys.executable, "-I", str(self.snapshot / ".rsi-runtime.py"),
                                 "read", "rsi-artifact-check", "--events", str(events), "--scope", "agent"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rsi-artifact-check", result.stdout)
        self.assertEqual(json.loads(events.read_text())["scope"], "agent")

    def test_unmanaged_broken_runtime_symlink_is_preserved(self):
        target = self.root / "other-runtime"
        target.mkdir()
        helper = target / ".rsi-runtime.py"
        helper.symlink_to(self.root / "missing-file")
        result = subprocess.run([sys.executable, str(ROOT / "scripts/skills.py"), "install",
                                 "--target", str(target)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(helper.is_symlink())
        self.assertFalse((target / "rsi-using-skills").exists())


if __name__ == "__main__":
    unittest.main()
