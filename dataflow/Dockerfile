# Dockerfile
FROM gcr.io/dataflow-templates-base/python312-template-launcher-base:latest

# Copy all pipeline source files
COPY pos_pipeline.py  /template/main.py
COPY file_reader.py   /template/file_reader.py
COPY schema_utils.py  /template/schema_utils.py
COPY setup.py         /template/setup.py
COPY requirements.txt /template/requirements.txt

# Install all dependencies at build time so workers don't fetch at runtime
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r /template/requirements.txt

ENV FLEX_TEMPLATE_PYTHON_PY_FILE=/template/pos_pipeline.py
ENV FLEX_TEMPLATE_PYTHON_SETUP_FILE=/template/setup.py

ENTRYPOINT ["/opt/google/dataflow/python_template_launcher"]