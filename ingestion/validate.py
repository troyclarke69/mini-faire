from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ingestion.paths import CONTRACTS_DIR


CONTRACT_BY_ENTITY = {
    "retailers": "retailer.schema.json",
    "products": "product.schema.json",
    "orders": "order.schema.json",
    "order_created": "order_created.schema.json",
}


@dataclass(frozen=True)
class ValidationResult:
    valid_records: list[dict[str, Any]]
    invalid_records: list[dict[str, Any]]


def load_json_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    raise ValueError(f"{path} must contain a JSON object or array of objects")


def load_validator(entity: str) -> Draft202012Validator:
    schema_path = CONTRACTS_DIR / CONTRACT_BY_ENTITY[entity]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_records(entity: str, records: list[dict[str, Any]]) -> ValidationResult:
    validator = load_validator(entity)
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []

    for index, record in enumerate(records):
        errors = sorted(validator.iter_errors(record), key=lambda error: error.path)
        if errors:
            invalid.append(
                {
                    "record_index": index,
                    "record": record,
                    "errors": [
                        {
                            "path": ".".join(str(part) for part in error.path),
                            "message": error.message,
                        }
                        for error in errors
                    ],
                }
            )
        else:
            valid.append(record)

    return ValidationResult(valid_records=valid, invalid_records=invalid)

