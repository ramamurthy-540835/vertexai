FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
COPY . .


# Configure pip to trust the Artifact Registry index
RUN pip install "cython<3.0.0" wheel
RUN pip install --upgrade pip setuptools wheel \
 && pip install "pyyaml==5.4.1" --no-build-isolation
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py  /app 
COPY  sync_config.ini /app

RUN pip install costco_lead_managment*.whl  --force-reinstall
#RUN pip install costco_lead_managment --index-url https://us-central1-python.pkg.dev/p-601-lab-bc-leads-mgmt/python-repo/simple/
#RUN pip install Costco_Lead_Managment --index-url https://us-central1-python.pkg.dev/p-601-lab-bc-leads-mgmt/python-repo/simple/


CMD [ "python", "main.py"  ]
