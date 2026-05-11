# Dockerfile for POS ETL Pipeline (Dataflow Flex Template)
FROM gcr.io/dataflow-templates-base/python312-template-launcher-base:latest

# Copy all pipeline source files
COPY pos_pipeline/    /template/pos_pipeline/
COPY setup.py         /template/setup.py
COPY requirements.txt /template/requirements.txt

# Install all dependencies at build time so workers don't fetch at runtime
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r /template/requirements.txt

# Tell the Flex Template launcher where to find things
ENV FLEX_TEMPLATE_PYTHON_PY_FILE=/template/pos_pipeline/main.py
ENV FLEX_TEMPLATE_PYTHON_SETUP_FILE=/template/setup.py
ENV FLEX_TEMPLATE_PYTHON_REQUIREMENTS_FILE=/template/requirements.txt

ENV PYTHONPATH=/template