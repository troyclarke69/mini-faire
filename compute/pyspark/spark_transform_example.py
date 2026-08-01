"""
Illustrative PySpark-style transform.

This repository runs locally with Polars by default to keep setup light. In a larger
deployment, the same contract and staging tables can feed a Spark job similar to this:

    orders = spark.table("marts.fact_orders")
    retailer_daily = (
        orders.groupBy("order_date", "retailer_id")
        .agg(
            countDistinct("order_id").alias("order_count"),
            sum("gross_amount").alias("gmv"),
            sum("estimated_profit").alias("estimated_profit"),
        )
    )
"""

