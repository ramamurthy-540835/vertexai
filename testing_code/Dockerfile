FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY test_gcs_to_drive.py .

CMD ["python", "test_gcs_to_drive.py"]
