# Use a lightweight Python image
FROM python:3.10.16-slim

# Set working directory
WORKDIR /app

# Copy files
COPY requirements.txt .
COPY run.py .
COPY costco_lead_managment-0.0.7-py3-none-any.whl   .

COPY ./config/* /app/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install costco_lead_managment-0.0.7-py3-none-any.whl  --force-reinstall

# Start the Flask app
CMD ["python", "run.py"]


