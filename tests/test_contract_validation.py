from ingestion.validate import validate_records
from ingestion.metadata import duration_ms


def test_invalid_order_is_quarantined():
    result = validate_records(
        "orders",
        [
            {
                "order_id": "ord_1",
                "retailer_id": "ret_1",
                "product_id": "prd_1",
                "order_ts": "2026-07-31T10:00:00Z",
                "quantity": 0,
                "gross_amount": 10.0,
                "discount_amount": 0.0,
                "status": "paid",
            }
        ],
    )

    assert result.valid_records == []
    assert result.invalid_records[0]["errors"]


def test_duration_ms_uses_utc_timestamps():
    assert (
        duration_ms(
            "2026-07-31T18:00:00+00:00",
            "2026-07-31T18:00:01.250000+00:00",
        )
        == 1250
    )
