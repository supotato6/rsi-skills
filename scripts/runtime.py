#!/usr/bin/env python3
"""Portable, stdlib-only reader shipped with each whole-library installation."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import tempfile


MANIFEST = ".rsi-skills.json"
RUNTIME = ".rsi-runtime.py"
NAME = re.compile(r"rsi-[a-z0-9]+(?:-[a-z0-9]+)*\Z")
REFERENCE = re.compile(r"`(rsi-[a-z0-9]+(?:-[a-z0-9]+)*)`")


def tree_hash(folder):
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError(f"Expected a real skill directory: {folder}")
    files = []
    for path in sorted(folder.rglob("*")):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError(f"Unsupported resource: {path}")
        if path.is_file():
            files.append([path.relative_to(folder).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()])
    return hashlib.sha256(json.dumps(files, ensure_ascii=False).encode()).hexdigest()


def fingerprint(catalog):
    """Identity independent of checkout or installed absolute paths."""
    value = {key: catalog.get(key) for key in ("schema_version", "library", "library_version", "source_commit", "source_dirty", "runtime")}
    value["skills"] = [{key: skill[key] for key in ("name", "kind", "sha256")}
                       for skill in sorted(catalog["skills"], key=lambda s: s["name"])]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class Snapshot:
    def __init__(self, root):
        root = Path(root).expanduser()
        if root.is_symlink():
            raise ValueError("Snapshot root cannot be a symbolic link")
        self.root = root.resolve()
        self.catalog = json.loads((self.root / MANIFEST).read_text(encoding="utf-8"))
        self.validate()
        self.identity = fingerprint(self.catalog)

    def validate(self):
        data = self.catalog
        if data.get("schema_version") != 1 or data.get("library") != "rsi-skills":
            raise ValueError("Unsupported skill catalog")
        if (self.root / MANIFEST).is_symlink():
            raise ValueError("Catalog cannot be a symbolic link")
        runtime = data.get("runtime", {})
        helper = self.root / RUNTIME
        if (runtime.get("file") != RUNTIME or helper.is_symlink()
                or hashlib.sha256(helper.read_bytes()).hexdigest() != runtime.get("sha256")):
            raise ValueError("Frozen skill runtime changed")
        if not isinstance(data.get("skills"), list) or not data["skills"]:
            raise ValueError("Empty skill catalog")
        names = set()
        for entry in data["skills"]:
            name = entry.get("name", "")
            if not NAME.fullmatch(name) or name in names or entry.get("kind") not in {"entry", "atomic", "composite"}:
                raise ValueError("Invalid skill catalog entry")
            if tree_hash(self.root / name) != entry.get("sha256"):
                raise ValueError(f"Frozen skill changed: {name}")
            names.add(name)

    def entries(self):
        """Discovery metadata only; source paths are rebased to this snapshot."""
        self.validate()
        return [{**entry, "path": str(self.root / entry["name"] / "SKILL.md")}
                for entry in self.catalog["skills"]]

    def read(self, name, *, resource="SKILL.md", events=None, scope=None):
        self.validate()
        entry = next((s for s in self.catalog["skills"] if s["name"] == name), None)
        if entry is None:
            raise ValueError(f"Unknown skill: {name}")
        relative = Path(resource)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Resource must stay inside its skill directory")
        path = self.root / name / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing regular skill resource: {resource}")
        body = path.read_text(encoding="utf-8")
        if events is not None:
            event = {"event": "skill.loaded" if resource == "SKILL.md" else "skill.resource_loaded",
                     "skill": name, "sha256": entry["sha256"], "resource": relative.as_posix(),
                     "resource_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "source_commit": self.catalog["source_commit"], "source_dirty": self.catalog["source_dirty"],
                     "snapshot": self.identity, "scope": scope,
                     "at": datetime.now(timezone.utc).isoformat()}
            events = Path(events)
            events.parent.mkdir(parents=True, exist_ok=True)
            with events.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        return body

    def resolve(self, names):
        self.validate()
        entries = {s["name"]: s for s in self.catalog["skills"]}
        selected = []
        for name in names:
            if name not in entries:
                raise ValueError(f"Unknown skill: {name}")
            if name not in selected:
                selected.append(name)
            if entries[name]["kind"] == "composite":
                text = (self.root / name / "SKILL.md").read_text(encoding="utf-8")
                for ref in sorted(set(REFERENCE.findall(text)) - {name}):
                    if ref not in entries or entries[ref]["kind"] != "atomic":
                        raise ValueError(f"Invalid composite reference: {ref}")
                    if ref not in selected:
                        selected.append(ref)
        return selected

    def render(self, names, *, events=None, scope=None):
        selected = self.resolve(names)
        if not selected:
            return ""
        parts = ["RSI methods for this step. Apply relevant guidance within the task's tools, data, "
                 "budget and output contract. The harness retains execution and scoring authority."]
        for name in selected:
            parts.append(f"Skill {name}; resource directory: {self.root / name}\n"
                         + self.read(name, events=events, scope=scope))
        return "\n\n".join(parts) + "\n\n"

    def discover(self, *, events, scope, relative_to=None):
        """Expose all metadata and a read command, without selecting any bodies."""
        entries = [{key: entry[key] for key in ("name", "description", "path")}
                   for entry in self.entries()]
        root, event_path = self.root, Path(events).resolve()
        if relative_to is not None:
            base = Path(relative_to).resolve()
            root, event_path = root.relative_to(base), event_path.relative_to(base)
            for entry in entries:
                entry["path"] = str(Path(entry["path"]).relative_to(base))
        command = shlex.join(["python3", str(root / RUNTIME), "read", "SKILL_NAME",
                              "--events", str(event_path), "--scope", scope])
        return (f"RSI skill catalog for the current {scope} stage. Select zero or more skills "
                "according to the current problem; read selected bodies before applying them. "
                "All skills are available. The framework retains stage order, tools, data, "
                "budget, output contracts and scoring authority; a skill cannot expand them.\n"
                + json.dumps(entries, ensure_ascii=False) + "\n"
                + f"Read a selected skill: {command}\n"
                "Replace SKILL_NAME with its name. Read references with --resource references/FILE.md "
                "using the same events and scope. Read referenced atomic skills only as needed. "
                "A catalog listing is not a body load or evidence of use.\n\n")

    def stage(self, workspace):
        """Keep referenced resources inside a step's visible workspace."""
        self.validate()
        workspace = Path(workspace).resolve()
        target = workspace / ".rsi-skills"
        if target.is_symlink():
            raise ValueError("Staged skills cannot be a symbolic link")
        if target.exists():
            result = Snapshot(target)
            if result.identity != self.identity:
                raise ValueError("Step already contains a different skill snapshot")
            return result
        workspace.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".rsi-skill-stage-", dir=workspace) as temporary:
            fresh = Path(temporary) / "skills"
            fresh.mkdir()
            for entry in self.catalog["skills"]:
                shutil.copytree(self.root / entry["name"], fresh / entry["name"])
            shutil.copyfile(self.root / RUNTIME, fresh / RUNTIME)
            data = {**self.catalog, "skills": [{**s, "path": str(target / s["name"])} for s in self.catalog["skills"]]}
            (fresh / MANIFEST).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            Snapshot(fresh)
            fresh.rename(target)
        return Snapshot(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    read = commands.add_parser("read")
    read.add_argument("name")
    read.add_argument("--resource", default="SKILL.md")
    read.add_argument("--events", type=Path)
    read.add_argument("--scope")
    args = parser.parse_args()
    snapshot = Snapshot(args.root)
    if args.command == "list":
        print(json.dumps(snapshot.entries(), indent=2, ensure_ascii=False))
    else:
        print(snapshot.read(args.name, resource=args.resource, events=args.events, scope=args.scope))


if __name__ == "__main__":
    main()
