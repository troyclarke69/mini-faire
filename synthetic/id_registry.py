"""Scans existing `data/batch` and `data/events` source JSON so the synthetic
generator can continue ID sequences and know the current retailer/product
universe, instead of guessing starting points or risking collisions on rerun."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ingestion.paths import DATA_DIR
from ingestion.validate import validate_records


ID_RE = {
    "retailer": re.compile(r"^ret_(\d+)$"),
    "product": re.compile(r"^prd_(\d+)$"),
    "brand": re.compile(r"^br_(\d+)$"),
}

ID_PREFIX = {"retailer": "ret", "product": "prd", "brand": "br", "order": "ord", "event": "evt"}

DEFAULT_NEXT_ID = {"retailer": 1001, "product": 2001, "brand": 501, "order": 3001, "event": 9001}


@dataclass
class Registry:
    next_ids: dict[str, int]
    retailers: dict[str, dict]
    products: dict[str, dict]
    brand_ids: list[str] = field(default_factory=list)

    def next_id(self, key: str) -> str:
        value = self.next_ids[key]
        self.next_ids[key] = value + 1
        return f"{ID_PREFIX[key]}_{value}"

    def peek_next_int(self, key: str) -> int:
        return self.next_ids[key]


def _load_records(root: Path) -> list[dict]:
    records: list[dict] = []
    if not root.exists():
        return records
    for path in sorted(root.glob("**/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        records.extend(payload if isinstance(payload, list) else [payload])
    return [record for record in records if isinstance(record, dict)]


def _max_bare_id(data_dir: Path, subfolders: list[str], key: str) -> int:
    pattern = re.compile(rf'"{key}"\s*:\s*"[a-zA-Z]+_(\d+)"')
    max_found = 0
    for sub in subfolders:
        root = data_dir / sub
        if not root.exists():
            continue
        for path in root.glob("**/*.json"):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for match in pattern.finditer(text):
                max_found = max(max_found, int(match.group(1)))
    return max_found


def build_registry(data_dir: Path = DATA_DIR) -> Registry:
    retailer_records = _load_records(data_dir / "batch" / "retailers")
    product_records = _load_records(data_dir / "batch" / "products")

    # Drop deliberately-invalid quarantine variants before indexing by ID.
    # make_invalid_variant() (see generator.py) copies its source record and
    # corrupts one field, so the broken sibling carries the SAME retailer_id/
    # product_id as the valid original. Without this filter, a last-write-wins
    # dict keyed by ID can let a corrupted record (e.g. inventory_count set to
    # the string "not-a-number") clobber the valid one, which then blows up
    # any downstream code - such as generate_dataset()'s `int(record.get(
    # "inventory_count", 0))` - that assumes registry records are well-typed.
    retailer_records = validate_records("retailers", retailer_records).valid_records
    product_records = validate_records("products", product_records).valid_records

    retailers: dict[str, dict] = {}
    for record in retailer_records:
        rid = record.get("retailer_id")
        if isinstance(rid, str) and ID_RE["retailer"].match(rid):
            retailers[rid] = record

    products: dict[str, dict] = {}
    brand_ids: set[str] = set()
    for record in product_records:
        pid = record.get("product_id")
        if isinstance(pid, str) and ID_RE["product"].match(pid):
            products[pid] = record
            bid = record.get("brand_id")
            if isinstance(bid, str) and ID_RE["brand"].match(bid):
                brand_ids.add(bid)

    next_ids = dict(DEFAULT_NEXT_ID)

    retailer_max = max(
        (int(ID_RE["retailer"].match(rid).group(1)) for rid in retailers), default=0
    )
    product_max = max((int(ID_RE["product"].match(pid).group(1)) for pid in products), default=0)
    brand_max = max((int(ID_RE["brand"].match(bid).group(1)) for bid in brand_ids), default=0)
    order_max = _max_bare_id(data_dir, ["batch", "events"], "order_id")
    event_max = _max_bare_id(data_dir, ["batch", "events"], "event_id")

    if retailer_max:
        next_ids["retailer"] = retailer_max + 1
    if product_max:
        next_ids["product"] = product_max + 1
    if brand_max:
        next_ids["brand"] = brand_max + 1
    if order_max:
        next_ids["order"] = order_max + 1
    if event_max:
        next_ids["event"] = event_max + 1

    return Registry(
        next_ids=next_ids,
        retailers=retailers,
        products=products,
        brand_ids=sorted(brand_ids),
    )
