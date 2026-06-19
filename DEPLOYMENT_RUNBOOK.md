# End-to-End Deployment Runbook

This document provides deployment guidelines for provisioning the infrastructure, compiling the pipeline components, and setting up the database layer for the Lead-to-POS Matching system.

---

## 1. Required GCP APIs

Ensure the following Google Cloud Service APIs are enabled in your host project before proceeding:
```bash
gcloud services enable \
    compute.googleapis.com \
    servicenetworking.googleapis.com \
    sqladmin.googleapis.com \
    run.googleapis.com \
    dataflow.googleapis.com \
    workflows.googleapis.com \
    workflowexecutions.googleapis.com \
    secretmanager.googleapis.com \
    artifactregistry.googleapis.com \
    aiplatform.googleapis.com \
    eventarc.googleapis.com \
    pubsub.googleapis.com
```

---

## 2. Service Accounts & Identity Federation

This project relies on **GCP Workload Identity Federation (WIF)** to securely authenticate GitHub Actions runners without hardcoding long-lived service account JSON keys.

### Essential Roles for the GitHub CI/CD Service Account:
*   `roles/owner` or `roles/editor` (to deploy general infrastructure resources)
*   `roles/iam.securityAdmin` (to apply service account bindings)
*   `roles/secretmanager.admin` (to configure secrets)
*   `roles/iam.workloadIdentityUser` (bound to the GitHub repository path)

### Application Execution Service Account (`gco-iam-svc-lead-mgmt-bc-<env>`):
Created by the `project_init` module to run Cloud Run Jobs, Cloud Workflows, and Dataflow workers. It requires:
*   `roles/storage.objectAdmin` on all environment storage buckets.
*   `roles/run.admin` to manage Cloud Run deployments.
*   `roles/cloudsql.client` to access Cloud SQL privately.
*   `roles/aiplatform.user` to invoke Vertex AI Embedding models.
*   `roles/secretmanager.secretAccessor` to read credentials.
*   `roles/workflows.admin` to deploy and execute orchestrators.

---

## 3. Core Infrastructure Deployment (Terraform)

Infrastructure deployment follows a strict environment-by-environment isolation design.

1.  **Navigate to the target environment's infrastructure directory:**
    ```bash
    cd terraform/environments/adt/infra/
    ```
2.  **Initialize the Terraform backend (pointing to GCS remote locks):**
    ```bash
    terraform init
    ```
3.  **Validate and inspect the resource plan:**
    ```bash
    terraform plan -var-file="adt.auto.tfvars"
    ```
4.  **Apply and provision the environment:**
    ```bash
    terraform apply -var-file="adt.auto.tfvars" -auto-approve
    ```

---

## 4. Database Setup and Migration

To perform schema migrations securely, the runners utilize the **Cloud SQL Auth Proxy** to route DDL/DML transactions privately.

1.  **Download the Cloud SQL Auth Proxy client:**
    ```bash
    curl -o cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.11.0/cloud-sql-proxy.linux.amd64
    chmod +x cloud-sql-proxy
    ```
2.  **Start the proxy background process (pointing to the private instance):**
    ```bash
    ./cloud-sql-proxy <GCP_PROJECT_ID>:us-central1:lead-mgmt-adt --port=5432 &
    ```
3.  **Apply DDL schemas and configurations:**
    Run the migrations using a standard Postgres client. Replace `$SCHEMA_NAME` dynamically inside scripts:
    ```bash
    export PGPASSWORD="<DB_PASSWORD>"
    psql -h 127.0.0.1 -U postgres -d lead-mgmt-db -f postgres_resources/lead_mgmt_schema_creation.sql
    psql -h 127.0.0.1 -U postgres -d lead-mgmt-db -f postgres_resources/costco_db_ddl.sql
    psql -h 127.0.0.1 -U postgres -d lead-mgmt-db -f postgres_resources/costco_db_dml.sql
    ```
4.  **Confirm IAM Authentication for DB users:**
    Ensure database IAM bindings exist for the app's service account:
    ```sql
    CREATE USER "gco-iam-svc-lead-mgmt-bc-adt@<GCP_PROJECT_ID>.iam" WITH LOGIN;
    GRANT rds_iam TO "gco-iam-svc-lead-mgmt-bc-adt@<GCP_PROJECT_ID>.iam";
    ```

---

## 5. Application Container Deployment

### Step A: Dataflow Flex Template Building
1.  **Build the Dataflow Python package wheel:**
    ```bash
    cd dataflow/
    python setup.py sdist bdist_wheel
    ```
2.  **Build and push the Dataflow Docker image:**
    ```bash
    docker build -t us-central1-docker.pkg.dev/<PROJECT_ID>/gcp-lead-mgmt/pos-etl:latest .
    docker push us-central1-docker.pkg.dev/<PROJECT_ID>/gcp-lead-mgmt/pos-etl:latest
    ```
3.  **Generate and publish the Flex Template spec:**
    ```bash
    gcloud dataflow flex-template build gs://gcp-gcs-lead-mgmt-adt/templates/pos-etl.json \
        --image "us-central1-docker.pkg.dev/<PROJECT_ID>/gcp-lead-mgmt/pos-etl:latest" \
        --sdk-language "PYTHON" \
        --metadata-file "metadata.json"
    ```

### Step B: Cloud Run Jobs Construction
1.  **Build Python wheel for core libraries:**
    ```bash
    cd lead_match_codebase/
    python -m build
    cp dist/*.whl ../lead_management_job/
    ```
2.  **Build and Push the job container:**
    ```bash
    cd ../lead_management_job/
    docker build -t us-central1-docker.pkg.dev/<PROJECT_ID>/gcp-lead-mgmt/lead-mgmt-job:latest .
    docker push us-central1-docker.pkg.dev/<PROJECT_ID>/gcp-lead-mgmt/lead-mgmt-job:latest
    ```
3.  **Deploy the job definitions to Cloud Run:**
    ```bash
    gcloud run jobs deploy snow-sync-job \
        --image us-central1-docker.pkg.dev/<PROJECT_ID>/gcp-lead-mgmt/lead-mgmt-job:latest \
        --service-account gco-iam-svc-lead-mgmt-bc-adt@<PROJECT_ID>.iam.gserviceaccount.com \
        --region us-central1
        
    gcloud run jobs deploy lead-match-job \
        --image us-central1-docker.pkg.dev/<PROJECT_ID>/gcp-lead-mgmt/lead-mgmt-job:latest \
        --service-account gco-iam-svc-lead-mgmt-bc-adt@<PROJECT_ID>.iam.gserviceaccount.com \
        --region us-central1
    ```

---

## 6. Workflow Deployments

Deploy the Cloud Workflows to route orchestration stages:
```bash
gcloud workflows deploy pos_ingestion_workflow \
    --source=terraform/modules/workflows/pos_dataflow_workflow.yaml \
    --service-account=gco-iam-svc-lead-mgmt-bc-adt@<PROJECT_ID>.iam.gserviceaccount.com \
    --location=us-central1

gcloud workflows deploy snow_sync_workflow \
    --source=terraform/modules/workflows/snow_sync_workflow.yaml \
    --service-account=gco-iam-svc-lead-mgmt-bc-adt@<PROJECT_ID>.iam.gserviceaccount.com \
    --location=us-central1

gcloud workflows deploy lead_match_workflow \
    --source=terraform/modules/workflows/lead_match_workflow.yaml \
    --service-account=gco-iam-svc-lead-mgmt-bc-adt@<PROJECT_ID>.iam.gserviceaccount.com \
    --location=us-central1
```
