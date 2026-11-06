#!/usr/bin/env python3
"""The single entry point. From a clean checkout, on any OS:

    python run.py              # set up (first run only) + run the whole pipeline

On the first run it creates ./.venv and installs the package with the exact tested dependency
versions from constraints.txt. Later runs reuse that environment, and re-install only if
pyproject.toml or constraints.txt changed. Only the standard library is used here, so nothing
needs to be installed beforehand except Python 3.11+.

Other commands go through the same entry point:

    python run.py test         # offline test suite (network access is blocked in tests)
    python run.py lint         # ruff check + format check
    python run.py ingest       # API -> raw layer only
    python run.py transform    # dbt build only: rebuild everything from raw, no API calls
                               #   (add --full-refresh to rebuild the MERGE-loaded fact too)
    python run.py export       # reporting views -> CSV
    python run.py docs         # dbt docs + lineage graph on http://localhost:8080
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
STAMP = VENV / ".wb-pipeline-installed"
MIN_PYTHON = (3, 11)
PIPELINE_COMMANDS = {"run", "ingest", "transform", "export"}
COMMANDS = PIPELINE_COMMANDS | {"test", "lint", "docs"}


def _venv_bin(name: str) -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / f"{name}.exe"
    return VENV / "bin" / name


def _install_fingerprint() -> str:
    h = hashlib.sha256()
    for f in ("pyproject.toml", "constraints.txt"):
        h.update((ROOT / f).read_bytes())
    return h.hexdigest()


def _call(cmd: list, **kwargs) -> int:
    return subprocess.call([str(c) for c in cmd], cwd=ROOT, **kwargs)


def ensure_environment() -> Path:
    """Create .venv and install pinned dependencies if needed. Returns the venv's python."""
    python = _venv_bin("python")
    if not python.exists():
        print(f"[run.py] creating virtual environment in {VENV} ...", flush=True)
        venv.EnvBuilder(with_pip=True, clear=True).create(VENV)

    fingerprint = _install_fingerprint()
    if not STAMP.exists() or STAMP.read_text().strip() != fingerprint:
        print("[run.py] installing dependencies (pinned in constraints.txt) ...", flush=True)
        pip = [python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
        if _call([*pip, "--upgrade", "pip"]) or _call([*pip, "-e", ".[dev]", "-c", "constraints.txt"]):
            sys.exit("[run.py] dependency installation failed - see the pip output above")
        STAMP.write_text(fingerprint)
    return python


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "run"
    if command in {"-h", "--help", "help"}:
        print(__doc__)
        return 0
    if command not in COMMANDS:
        print(f"unknown command {command!r}\n{__doc__}", file=sys.stderr)
        return 2
    if sys.version_info < MIN_PYTHON:
        found = ".".join(map(str, sys.version_info[:3]))
        print(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required (found {found}). "
            "Re-run with a newer interpreter, e.g. `python3.12 run.py`.",
            file=sys.stderr,
        )
        return 2

    python = ensure_environment()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    extra = argv[1:]

    if command in PIPELINE_COMMANDS:
        return _call([python, "-m", "wb_pipeline", command, *extra], env=env)
    if command == "test":
        return _call([python, "-m", "pytest", *extra], env=env)
    if command == "lint":
        ruff = [python, "-m", "ruff"]
        return _call([*ruff, "check", "src", "tests", "run.py"]) or _call(
            [*ruff, "format", "--check", "src", "tests", "run.py"]
        )
    # docs
    dbt_env = dict(env, WB_DUCKDB_PATH=str(ROOT / "data" / "warehouse.duckdb"))
    dbt = [_venv_bin("dbt")]
    dbt_dirs = ["--project-dir", ROOT / "dbt", "--profiles-dir", ROOT / "dbt"]
    return _call([*dbt, "docs", "generate", *dbt_dirs], env=dbt_env) or _call(
        [*dbt, "docs", "serve", *dbt_dirs], env=dbt_env
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
