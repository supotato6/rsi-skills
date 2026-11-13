"""Shared dependency for RSI frameworks; no model calls or runtime configuration."""
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys

from .scripts.runtime import Snapshot, fingerprint
from .scripts.skills import catalog as _catalog

__version__ = "0.2.1"


def catalog(source=None):
    """Describe the complete bundled library without loading skill bodies."""
    return _catalog(Path(source).resolve() if source is not None else Path(__file__).resolve().parent)


def install(target, *, expected_commit=None, source=None):
    """Freeze the entire dependency into an explicit task-owned directory."""
    command = [sys.executable, str(Path(__file__).parent / "scripts/skills.py")]
    if source is not None:
        command += ["--source", str(Path(source).resolve())]
    command += ["install", "--target", str(Path(target).resolve())]
    if expected_commit is not None:
        command += ["--expect-commit", expected_commit]
    subprocess.run(command, check=True, capture_output=True, text=True)
    return open_snapshot(target)


def open_snapshot(target):
    """Use the loader frozen with this snapshot, including after dependency upgrades."""
    target = Path(target).resolve()
    record = json.loads((target / ".rsi-skills.json").read_text(encoding="utf-8"))
    helper = target / ".rsi-runtime.py"
    if (helper.is_symlink() or record.get("runtime", {}).get("file") != helper.name
            or hashlib.sha256(helper.read_bytes()).hexdigest() != record["runtime"].get("sha256")):
        raise ValueError("Frozen skill runtime changed")
    return runpy.run_path(str(helper))["Snapshot"](target)


def load_config(value):
    """Resolve a framework-owned auto-selection config; never duplicate the Git pin.

    File-relative paths are independent of the caller's working directory. Dicts
    use the caller's working directory. Source existence is checked at install,
    so resuming a frozen run does not require the original checkout.
    """
    base = Path.cwd()
    if isinstance(value, (str, Path)):
        path = Path(value).expanduser().resolve()
        base = path.parent
        value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or set(value) != {"library_path", "selection"}
            or value.get("selection") != "auto"
            or not isinstance(value.get("library_path"), str) or not value["library_path"].strip()):
        raise ValueError("Skills config requires library_path and selection=auto")
    return {"library_path": str((base / Path(value["library_path"]).expanduser()).resolve()),
            "selection": "auto"}


__all__ = ["Snapshot", "catalog", "fingerprint", "install", "load_config", "open_snapshot"]
