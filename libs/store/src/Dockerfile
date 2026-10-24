FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY nafa_ingest ./nafa_ingest
COPY app ./app
COPY config ./config
RUN pip install --no-cache-dir .
COPY docs ./docs
RUN mkdir -p data/input
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
