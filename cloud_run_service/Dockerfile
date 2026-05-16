FROM python:3.12-slim

WORKDIR /app

# Install deps first for better layer caching
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Cloud Run sets PORT env var; gunicorn binds to it.
# 2 workers, 4 threads = handle reasonable concurrent requests without DB pool exhaustion.
CMD exec gunicorn \
    --bind 0.0.0.0:${PORT:-8080} \
    --workers 2 \
    --threads 4 \
    --timeout 60 \
    main:app