# Use an official lightweight Python image
FROM python:3.10.16-slim

# Set the working directory
WORKDIR /app


# Copy requirements.txt from the parent context (now available!)
# COPY requirements.txt .
COPY . .

# Install required packages
# RUN pip install --no-cache-dir kfp google-cloud-aiplatform pandas numpy google-cloud-storage unidecode \
#     cloud-sql-python-connector[pg8000] sqlalchemy pgvector google-cloud-secret-manager
RUN pip install --no-cache-dir -r requirements.txt


RUN pip install costco_lead_managment-0.0.7-py3-none-any.whl  --force-reinstall

# Ensure no unnecessary files are in the final image
RUN apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
