#!/usr/bin/env python3
"""Validate, discover and copy an entire RSI skill library using only stdlib."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


LIBRARY = "rsi-skills"
MANIFEST = ".rsi-skills.json"
RUNTIME = ".rsi-runtime.py"
KINDS = {"entry", "atomic", "composite"}
NAME = re.compile(r"rsi-[a-z0-9]+(?:-[a-z0-9]+)*\Z")
REFERENCE = re.compile(r"`(rsi-[a-z0-9]+(?:-[a-z0-9]+)*)`")


def tree_hash(folder):
    """Hash relative names and file bytes; reject links and special files."""
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError(f"Expected a real skill directory: {folder}")
    files = []
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are not supported: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"Special files are not supported: {path}")
        files.append([path.relative_to(folder).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()])
    return hashlib.sha256(json.dumps(files, ensure_ascii=False).encode()).hexdigest()


def frontmatter(path):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError(f"Missing YAML frontmatter: {path}")
    end = lines[1:].index("---") + 1
    fields = {}
    for line in lines[1:end]:
        match = re.fullmatch(r"(name|description):\s+(.+)", line)
        if not match or match[1] in fields:
            raise ValueError(f"Use unique single-line name and description fields: {path}")
        value = match[2].strip()
        if value.startswith('"'):
            value = json.loads(value)
        elif value.startswith("'"):
            if not value.endswith("'"):
                raise ValueError(f"Unclosed YAML string: {path}")
            value = value[1:-1].replace("''", "'")
        elif ": " in value or " #" in value or value[0] in "[{>|&*!":
            raise ValueError(f"Quote complex frontmatter values as JSON strings: {path}")
        if not isinstance(value, str) or not value:
            raise ValueError(f"Expected a non-empty string: {path}")
        fields[match[1]] = value
    if set(fields) != {"name", "description"} or not "\n".join(lines[end + 1:]).strip():
        raise ValueError(f"Required name, description and instructions: {path}")
    return fields, "\n".join(lines[end + 1:])


def revision(source):
    """Do not accidentally report a containing repository's commit."""
    try:
        top = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "--show-toplevel"], stderr=subprocess.DEVNULL, text=True,
        ).strip()
        if Path(top).resolve() != source:
            return None, None
        commit = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True,
        ).strip()
        dirty = bool(subprocess.check_output(
            ["git", "-C", str(source), "status", "--porcelain"], text=True,
        ).strip())
        return commit, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, None


def catalog(source):
    root = source / "skills"
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Missing real skills directory: {root}")
    skills, references = {}, {}
    for kind_dir in sorted(root.iterdir()):
        if kind_dir.name not in KINDS or kind_dir.is_symlink() or not kind_dir.is_dir():
            raise ValueError(f"Unexpected skill category: {kind_dir}")
        for folder in sorted(kind_dir.iterdir()):
            digest = tree_hash(folder)
            fields, body = frontmatter(folder / "SKILL.md")
            name = fields["name"]
            if not NAME.fullmatch(name) or len(name) > 64 or name != folder.name or name in skills:
                raise ValueError(f"Invalid, mismatched or duplicate skill name: {name}")
            if len(fields["description"]) > 500:
                raise ValueError(f"Description exceeds 500 characters: {name}")
            references[name] = set(REFERENCE.findall(body)) - {name}
            skills[name] = {**fields, "kind": kind_dir.name, "path": str(folder), "sha256": digest}
    if not skills:
        raise ValueError("The library contains no skills")
    for name, refs in references.items():
        missing = refs - skills.keys()
        if missing:
            raise ValueError(f"{name} references missing skills: {', '.join(sorted(missing))}")
        kind = skills[name]["kind"]
        if kind == "atomic" and refs:
            raise ValueError(f"Atomic skill delegates to other skills: {name}")
        if kind == "composite" and (not refs or any(skills[ref]["kind"] != "atomic" for ref in refs)):
            raise ValueError(f"Composite must reference atomic skills only: {name}")
    commit, dirty = revision(source)
    data = {"schema_version": 1, "library": LIBRARY, "library_version": "0.2.1", "source_commit": commit,
            "source_dirty": dirty, "skills": [skills[name] for name in sorted(skills)]}
    helper = source / "scripts/runtime.py"
    if helper.exists() or helper.is_symlink():
        if helper.is_symlink() or not helper.is_file():
            raise ValueError("Runtime helper must be a regular file")
        data["runtime"] = {"file": RUNTIME, "sha256": hashlib.sha256(helper.read_bytes()).hexdigest()}
    return data


def destination(raw, source):
    path = Path(raw).expanduser()
    if path.is_symlink():
        raise ValueError(f"Target cannot be a symbolic link: {path}")
    target = path.resolve()
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("Source and target directories must not overlap")
    if target.exists() and not target.is_dir():
        raise ValueError(f"Target is not a directory: {target}")
    return target


def read_installed(target):
    path = target / MANIFEST
    if path.is_symlink():
        raise ValueError("Installation record cannot be a symbolic link")
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("library") != LIBRARY:
        raise ValueError("Unrecognized installation record")
    if "runtime" in data:
        helper = target / RUNTIME
        if (not isinstance(data["runtime"], dict) or data["runtime"].get("file") != RUNTIME or helper.is_symlink()
                or not helper.is_file() or hashlib.sha256(helper.read_bytes()).hexdigest() != data["runtime"].get("sha256")):
            raise ValueError("Managed runtime was locally modified; preserve your changes first")
    entries = data.get("skills")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Invalid installation record skill list")
    names = set()
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("Invalid installation record entry")
        name = item.get("name")
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in names:
            raise ValueError("Invalid or duplicate managed skill name")
        if item.get("path") != str(target / name) or item.get("kind") not in KINDS:
            raise ValueError(f"Invalid managed skill location or category: {name}")
        digest = item.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid managed skill hash: {name}")
        if tree_hash(target / name) != digest:
            raise ValueError(f"Managed skill was locally modified: {name}; preserve your changes first")
        names.add(name)
    return data


def transact(target, old, new=None):
    """Stage a complete copy, then replace managed paths; rollback OS errors.

    Updates require exclusive access to the target. This is not a crash journal
    or protection against concurrent writers; stop runtime writers first.
    """
    target.mkdir(parents=True, exist_ok=True)
    old_names = [skill["name"] for skill in old["skills"]] if old else []
    if old and "runtime" in old:
        old_names.append(RUNTIME)
    with tempfile.TemporaryDirectory(prefix=".rsi-stage-", dir=target) as temporary:
        stage = Path(temporary)
        fresh, backup = stage / "new", stage / "old"
        fresh.mkdir()
        backup.mkdir()
        if new:
            for skill in new["skills"]:
                name = skill["name"]
                shutil.copytree(skill["path"], fresh / name)
                if tree_hash(fresh / name) != skill["sha256"]:
                    raise ValueError(f"Source changed during copying: {name}")
            if "runtime" in new:
                helper = Path(new["skills"][0]["path"]).parents[2] / "scripts/runtime.py"
                shutil.copyfile(helper, fresh / RUNTIME)
                if hashlib.sha256((fresh / RUNTIME).read_bytes()).hexdigest() != new["runtime"]["sha256"]:
                    raise ValueError("Runtime changed during copying")
            installed = {**new, "skills": [{**s, "path": str(target / s["name"])} for s in new["skills"]]}
            (fresh / MANIFEST).write_text(json.dumps(installed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        moved_old, moved_new = [], []
        try:
            for name in old_names + ([MANIFEST] if old else []):
                os.replace(target / name, backup / name)
                moved_old.append(name)
            if new:
                for name in [skill["name"] for skill in new["skills"]] + ([RUNTIME] if "runtime" in new else []) + [MANIFEST]:
                    os.replace(fresh / name, target / name)
                    moved_new.append(name)
        except OSError:
            for name in reversed(moved_new):
                os.replace(target / name, fresh / name)
            for name in reversed(moved_old):
                os.replace(backup / name, target / name)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1], help="Library repository root")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Validate source skills and one-level composition")
    listing = commands.add_parser("list", help="Print discovery metadata without loading skill bodies")
    listing.add_argument("--json", action="store_true", help="Emit the complete catalog as JSON")
    installing = commands.add_parser("install", help="Copy the entire library into an explicit target")
    installing.add_argument("--target", required=True, help="Runtime skills directory")
    installing.add_argument("--update", action="store_true", help="Replace only unchanged, previously managed skills")
    installing.add_argument("--expect-commit", help="Require this exact clean Git commit")
    uninstalling = commands.add_parser("uninstall", help="Remove only unchanged managed skills")
    uninstalling.add_argument("--target", required=True, help="Previously used runtime skills directory")
    args = parser.parse_args()
    try:
        source = args.source.expanduser().resolve()
        if args.command in {"install", "uninstall"}:
            target = destination(args.target, source)
            old = read_installed(target)
            if args.command == "uninstall":
                if old:
                    transact(target, old)
                print("Removed managed RSI skills" if old else "No managed RSI skills installed")
                return 0
        data = catalog(source)
        if args.command == "install" and args.expect_commit:
            if data["source_commit"] != args.expect_commit or data["source_dirty"] is not False:
                raise ValueError("Expected a clean checkout of --expect-commit")
        if args.command == "check":
            print(f"Validated {len(data['skills'])} skills")
        elif args.command == "list":
            if args.json:
                print(json.dumps(data, indent=2, ensure_ascii=False))
            else:
                for skill in data["skills"]:
                    print(f"{skill['name']} [{skill['kind']}] — {skill['description']}")
        elif args.command == "install":
            if "runtime" in data and ((target / RUNTIME).exists() or (target / RUNTIME).is_symlink()) and not (old and "runtime" in old):
                raise ValueError("Refusing to overwrite an unmanaged runtime helper")
            previous = {item["name"] for item in old["skills"]} if old else set()
            for skill in data["skills"]:
                path = target / skill["name"]
                if (path.exists() or path.is_symlink()) and skill["name"] not in previous:
                    raise ValueError(f"Refusing to overwrite unmanaged path: {path}")
            normalized = {**data, "skills": [{**s, "path": str(target / s["name"])} for s in data["skills"]]}
            if old == normalized:
                print("Already installed; no changes")
                return 0
            if old and not args.update:
                raise ValueError("Library changed; use install --update after stopping runtime writers")
            transact(target, old, data)
            print(f"Installed {len(data['skills'])} skills into {target}")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
