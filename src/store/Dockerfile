FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 NAFA_DATA_DIR=/data NAFA_DB_PATH=/data/nafa.db
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir -r requirements.txt
COPY . .
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "app.cli", "serve"]
