FROM python:3.13-slim

WORKDIR /app

#configuration files,wheel file,properties file,requirements.txt,run.py,main.py
COPY . . 

RUN pip install "cython<3.0.0" wheel
RUN pip install --upgrade pip setuptools wheel \
 && pip install "pyyaml==5.4.1" --no-build-isolation
 
RUN pip install --no-cache-dir -r requirements.txt
 
RUN pip install costco_lead_managment-0.0.14-py3-none-any.whl  --force-reinstall