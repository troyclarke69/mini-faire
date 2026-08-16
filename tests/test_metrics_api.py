import json

from api.metrics_api import product_reorder_risk, retailer_daily, retailer_health


def test_retailer_daily_returns_json_serializable_rows():
    rows = retailer_daily()

    assert rows
    assert rows[0]["retailer_id"].startswith("ret_")
    json.dumps(rows)


def test_retailer_health_returns_json_serializable_rows():
    rows = retailer_health()

    assert rows
    assert "retailer_health_score" in rows[0]
    json.dumps(rows)


def test_product_reorder_risk_returns_json_serializable_rows():
    rows = product_reorder_risk()

    assert rows
    assert rows[0]["reorder_risk_band"] in {"low", "medium", "high"}
    json.dumps(rows)
