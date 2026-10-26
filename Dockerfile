FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.10 /uv /usr/local/bin/uv
WORKDIR /app
COPY src/store/pyproject.toml src/store/uv.lock src/store/
RUN uv sync --project src/store --frozen --no-dev --group preparation
COPY src/store/app src/store/app
COPY src/store/config src/store/config
COPY src/store/data/ref src/store/data/ref
COPY src/store/web src/store/web
COPY src/report_scripts/pe src/report_scripts/pe
COPY src/report_scripts/prompts src/report_scripts/prompts
COPY src/report_scripts/config src/report_scripts/config
COPY src/report_scripts/reference src/report_scripts/reference
COPY src/da_scripts/analytics src/da_scripts/analytics
COPY src/da_scripts/config src/da_scripts/config
COPY src/da_scripts/ref_data src/da_scripts/ref_data
COPY scripts/prepare_runtime.py scripts/container_start.py scripts/
RUN mkdir -p /data && chown 1000:1000 /data
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src/store:/app/src/report_scripts NAFA_BACKEND_ROOT=/app/src/store NAFA_ROOT=/app/src/store NAFA_DATA_DIR=/data NAFA_DB_PATH=/data/nafa.db
USER 1000:1000
EXPOSE 8000
CMD ["/app/src/store/.venv/bin/python", "scripts/container_start.py"]
