# ML training/inference (PHASE7-DEPLOYMENT.md Section 1) -
# orchestration/ml_training_flow.py and ml_inference_flow.py, plus (Phase 7)
# ml/tenant_models/tenant_forecasting.py's per-tenant training. Separate
# image from Dockerfile.orchestration because this is the one service that
# needs the heavier [ml] dependency set (numpy/scikit-learn, optionally
# statsmodels/xgboost via [ml-extra]) - keeping it out of the
# orchestration/streaming images keeps those smaller and faster to build.
#
# Same build-untested caveat as this directory's other Dockerfiles.

FROM python:3.12-slim AS base
WORKDIR /app

COPY pyproject.toml README.md ./
COPY anomalies/ ./anomalies/
COPY config/ ./config/
COPY ingestion/ ./ingestion/
COPY ml/ ./ml/
COPY multi_tenant/ ./multi_tenant/
COPY orchestration/ ./orchestration/
COPY warehouse/ ./warehouse/

RUN pip install --no-cache-dir -e ".[ml]"
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1

# Overridden per-run in docker-compose.cloud.yaml / a scheduled job, e.g.:
#   command: ["orchestration/ml_inference_flow.py"]
#   command: ["-m", "ml.tenant_models.tenant_forecasting"]
ENTRYPOINT ["python"]
CMD ["orchestration/ml_training_flow.py"]
