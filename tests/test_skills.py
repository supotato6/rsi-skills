"""Installer contracts exercised through the public CLI and real filesystems."""
import json
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


CLI = Path(__file__).resolve().parents[1] / "scripts" / "skills.py"


class SkillsCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rsi skills ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source library"
        self.target = self.root / "runtime skills"
        self.write_skill("atomic", "rsi-a")
        self.write_skill("atomic", "rsi-b")
        self.write_skill("composite", "rsi-combo", "Use `rsi-a` and `rsi-b`.")
        self.write_skill("entry", "rsi-entry", "Select `rsi-combo` or `rsi-a`.")

    def write_skill(self, kind, name, body="Return an explicit, evidence-backed result."):
        folder = self.source / "skills" / kind / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Use when the {name} capability applies.\n---\n\n{body}\n",
            encoding="utf-8",
        )
        return folder

    def run_cli(self, *args, ok=True):
        self.assertTrue(CLI.is_file(), "The skills CLI has not been implemented")
        result = subprocess.run(
            [sys.executable, str(CLI), "--source", str(self.source), *args],
            capture_output=True, text=True,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def install(self, *args, ok=True):
        return self.run_cli("install", "--target", str(self.target), *args, ok=ok)

    def manifest(self):
        return json.loads((self.target / ".rsi-skills.json").read_text())

    def test_catalog_and_whole_library_install(self):
        self.run_cli("check")
        catalog = json.loads(self.run_cli("list", "--json").stdout)
        self.assertEqual(len(catalog["skills"]), 4)
        resources = self.source / "skills/atomic/rsi-a/references"
        resources.mkdir()
        (resources / "example.txt").write_text("Reusable resource")
        self.install()
        manifest = self.manifest()
        self.assertEqual(len(manifest["skills"]), 4)
        for skill in manifest["skills"]:
            folder = self.target / skill["name"]
            self.assertTrue((folder / "SKILL.md").is_file())
            self.assertEqual(skill["path"], str(folder.resolve()))
            self.assertEqual(len(skill["sha256"]), 64)
        self.assertEqual((self.target / "rsi-a/references/example.txt").read_text(), "Reusable resource")
        self.assertFalse((self.target / "atomic").exists())

    def test_repeat_is_idempotent_and_update_requires_flag(self):
        self.install()
        before = (self.target / ".rsi-skills.json").read_bytes()
        self.install()
        self.assertEqual((self.target / ".rsi-skills.json").read_bytes(), before)
        self.write_skill("atomic", "rsi-a", "A new method.")
        self.install(ok=False)
        self.assertNotIn("A new method", (self.target / "rsi-a/SKILL.md").read_text())
        self.install("--update")
        self.assertIn("A new method", (self.target / "rsi-a/SKILL.md").read_text())

    def test_update_removes_deleted_skills_and_preserves_unrelated(self):
        self.install()
        (self.target / "unrelated").mkdir()
        (self.target / "unrelated/keep").write_text("keep")
        shutil.rmtree(self.source / "skills/atomic/rsi-b")
        self.write_skill("composite", "rsi-combo", "Use `rsi-a`.")
        self.install("--update")
        self.assertFalse((self.target / "rsi-b").exists())
        self.assertEqual((self.target / "unrelated/keep").read_text(), "keep")

    def test_collision_is_detected_before_any_skill_is_written(self):
        (self.target / "rsi-b").mkdir(parents=True)
        (self.target / "rsi-b/keep").write_text("user content")
        self.install(ok=False)
        self.assertFalse((self.target / "rsi-a").exists())
        self.assertFalse((self.target / ".rsi-skills.json").exists())
        self.assertEqual((self.target / "rsi-b/keep").read_text(), "user content")

    def test_local_edits_block_update_and_uninstall(self):
        self.install()
        local = self.target / "rsi-a/SKILL.md"
        local.write_text("my local changes")
        self.install("--update", ok=False)
        self.run_cli("uninstall", "--target", str(self.target), ok=False)
        self.assertEqual(local.read_text(), "my local changes")
        self.assertTrue((self.target / "rsi-b").is_dir())

    def test_uninstall_preserves_unrelated_and_does_not_need_source(self):
        self.install()
        (self.target / "other.txt").write_text("keep")
        shutil.rmtree(self.source)
        self.run_cli("uninstall", "--target", str(self.target))
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ["other.txt"])
        self.run_cli("uninstall", "--target", str(self.target))

    def test_missing_dependency_and_nested_composite_rejected(self):
        self.write_skill("composite", "rsi-combo", "Use `rsi-absent`.")
        self.run_cli("check", ok=False)
        self.write_skill("composite", "rsi-combo", "Use `rsi-a`.")
        self.write_skill("composite", "rsi-nested", "Use `rsi-combo`.")
        self.run_cli("check", ok=False)

    def test_invalid_frontmatter_and_duplicate_name_rejected(self):
        path = self.source / "skills/atomic/rsi-a/SKILL.md"
        path.write_text("No frontmatter")
        self.run_cli("check", ok=False)
        self.write_skill("atomic", "rsi-a")
        self.write_skill("composite", "rsi-a", "Use `rsi-b`.")
        self.run_cli("check", ok=False)

    def test_target_and_source_symlinks_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.target.symlink_to(outside, target_is_directory=True)
        self.install(ok=False)
        self.assertEqual(list(outside.iterdir()), [])
        self.target.unlink()
        (self.source / "skills/atomic/rsi-a/escape").symlink_to(outside, target_is_directory=True)
        self.install(ok=False)
        self.assertFalse(self.target.exists())

    def test_managed_symlink_and_tampered_manifest_rejected(self):
        self.install()
        external = self.root / "external.md"
        external.write_text("keep")
        local = self.target / "rsi-a/SKILL.md"
        local.unlink()
        local.symlink_to(external)
        self.run_cli("uninstall", "--target", str(self.target), ok=False)
        self.assertEqual(external.read_text(), "keep")
        local.unlink()
        shutil.copyfile(self.source / "skills/atomic/rsi-a/SKILL.md", local)
        manifest = self.manifest()
        manifest["skills"][0]["name"] = "../external"
        (self.target / ".rsi-skills.json").write_text(json.dumps(manifest))
        self.install("--update", ok=False)
        self.assertTrue((self.target / "rsi-b").exists())

    def test_source_overlap_and_missing_target_rejected(self):
        self.run_cli("install", ok=False)
        self.run_cli("install", "--target", str(self.source / "installed"), ok=False)
        self.run_cli("install", "--target", str(self.root), ok=False)
        self.assertTrue((self.source / "skills/atomic/rsi-a/SKILL.md").is_file())

    def test_failed_update_restores_previous_installation(self):
        self.install()
        before = {p.relative_to(self.target): p.read_bytes()
                  for p in self.target.rglob("*") if p.is_file()}
        self.write_skill("atomic", "rsi-a", "A new method.")
        spec = importlib.util.spec_from_file_location("rsi_skills_cli", CLI)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        old = module.read_installed(self.target.resolve())
        new = module.catalog(self.source.resolve())
        real_replace = module.os.replace
        calls = 0

        def fail_once(src, dst):
            nonlocal calls
            calls += 1
            if calls == 7:
                raise OSError("Injected disk failure during replacement")
            return real_replace(src, dst)

        with patch.object(module.os, "replace", side_effect=fail_once):
            with self.assertRaisesRegex(OSError, "Injected disk failure"):
                module.transact(self.target.resolve(), old, new)
        after = {p.relative_to(self.target): p.read_bytes()
                 for p in self.target.rglob("*") if p.is_file()}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
