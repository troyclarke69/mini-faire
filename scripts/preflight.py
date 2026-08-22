from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def assert_supported_runtime() -> None:
    if sys.version_info >= (3, 13):
        raise SystemExit(
            "Mini Faire currently expects Python 3.10-3.12 because DuckDB's compiled "
            "Windows extension can fail to import under Python 3.13. Recreate the venv "
            "with: py -3.12 -m venv .venv"
        )


def assert_duckdb_extension_matches_python() -> None:
    spec = importlib.util.find_spec("duckdb")
    if spec is None or spec.origin is None:
        raise SystemExit(
            "DuckDB is not installed in this environment. Run: "
            '.\\.venv\\Scripts\\python.exe -m pip install -e ".[dev,mongo]"'
        )

    site_packages = Path(spec.origin).resolve().parents[1]
    expected_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    extensions = list(site_packages.glob("_duckdb*.pyd"))

    if not extensions:
        raise SystemExit(
            "DuckDB is installed, but its compiled _duckdb extension is missing. "
            "Reinstall it with: .\\.venv\\Scripts\\python.exe -m pip install --force-reinstall duckdb"
        )

    if not any(expected_tag in extension.name for extension in extensions):
        found = ", ".join(extension.name for extension in extensions)
        raise SystemExit(
            f"DuckDB extension does not match this Python runtime. Expected a {expected_tag} "
            f"extension but found: {found}. Recreate the venv with Python 3.12: "
            "py -3.12 -m venv .venv"
        )


def run_preflight() -> None:
    assert_supported_runtime()
    assert_duckdb_extension_matches_python()

