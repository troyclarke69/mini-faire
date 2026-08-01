from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_valid_and_quarantine(
    *,
    valid_path: Path,
    quarantine_path: Path,
    valid_records: list[dict[str, Any]],
    invalid_records: list[dict[str, Any]],
) -> None:
    write_json(valid_path, valid_records)
    write_json(quarantine_path, invalid_records)

