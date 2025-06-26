# GitHub Actions Workflows Overview

This repository uses GitHub Actions to automate infrastructure provisioning, schema updates, container builds, and job deployments for multiple GCP environments (`adt`, `qat`, `spt`).

---

## Workflow Summary

| Workflow                        | Purpose                          | Environments         | Triggered On                                  | Tools Used                    |
|--------------------------------|----------------------------------|----------------------|-----------------------------------------------|-------------------------------|
| `lead_mgmt_image.yml`          | Build & push container image     | All                  | Push to `feature/**`                          | Docker, GitHub & GCP Registry |
| `provision_adt.yml`            | Provision infrastructure         | `adt`                | Push/PR to `feature/**`                       | Terraform                     |
| `provision_adt_schema.yml`     | Apply PostgreSQL schema          | `adt`                | Push/PR to `feature/**`                       | Terraform, Cloud SQL Proxy    |
| `provision_qat.yml`            | Provision infrastructure         | `qat`                | Push/PR to `main`                             | Terraform                     |
| `provision_spt.yml`            | Provision infrastructure         | `spt`                | Git tag `tag_spt`                             | Terraform                     |
| `snow_sync_job_deployment.yml`| Deploy Cloud Run Snow Sync Job   | `adt`, `qat`, `spt`  | Push to `main`, `feature/**`, or tag `tag_*`  | Cloud Run, Scheduler          |
| `lead_match_job_deployment.yml`| Deploy Lead Match Cloud Run Job | `adt`, `qat`, `spt`  | Triggered by `workflow_call`                 | Cloud Run                     |

---

##  Workflow Details

###  1. `lead_mgmt_image.yml`
Builds and pushes a Docker image used in lead management jobs.

**Trigger Conditions:**
- Push to `feature/**`
- Path changes in:
  - `.github/workflows/lead_mgmt_image.yml`
  - `lead_management_job/**`
  - `lead_match_codebase/**`

**Main Actions:**
- Builds a Python wheel from `lead_match_codebase`
- Injects it into a Docker container
- Pushes image to:
  - GitHub Container Registry (`ghcr.io`)
  - Google Artifact Registry
- Triggers:
  - `lead_match_job_deployment.yml`
  - `snow_sync_job_deployment.yml`

---

###  2. `provision_adt.yml`
Provisions cloud infrastructure for the `adt` environment.

**Triggered By:**
- Push or pull request to `feature/**`
- Changes in:
  - `terraform/environments/adt/infra/**`
  - `terraform/modules/**`
  - `.github/workflows/provision_adt.yml`

**Highlights:**
- Authenticates using Workload Identity
- Initializes and applies Terraform

---

###  3. `provision_adt_schema.yml`
Applies schema changes to PostgreSQL using Cloud SQL Auth Proxy.

**Triggered By:**
- Push or pull request to `feature/**`
- Changes in:
  - `terraform/environments/adt/schema/**`
  - `postgres_resources/**.sql`
  - `.github/workflows/provision_adt_schema.yml`

**Steps:**
- Authenticates to GCP
- Starts Cloud SQL Proxy
- Executes SQL with `psql`
- Applies Terraform schema definitions

---

###  4. `provision_qat.yml`
Provisions infrastructure in the `qat` environment.

**Triggered By:**
- Push or PR to `main`
- Changes in:
  - `terraform/environments/qat/infra/**`
  - `terraform/modules/**`
  - `.github/workflows/provision_qat.yml`

**Details:**
- Uses Terraform for environment provisioning
- Uses matrix strategy for future scalability

---

###  5. `provision_spt.yml`
Provisions `spt` infrastructure using Git tags.

**Triggered By:**
- Git tag `tag_spt`
- Path changes in:
  - `terraform/environments/adt/infra/**`
  - `terraform/modules/**`
  - `.github/workflows/provision_adt.yml`

**Highlights:**
- Used for staging deployments
- Terraform `init`, `plan`, and `apply`

---

###  6. `snow_sync_job_deployment.yml`
Deploys the Cloud Run job for Snowflake sync.

**Triggered By:**
- `workflow_call` from another workflow
- Push to:
  - `main`
  - `feature/**`
  - Git tags: `tag_spt`, `tag_prod`

**Features:**
- Detects environment (`adt`, `qat`, or `spt`) dynamically
- Uses Cloud Run Jobs + GCP Scheduler
- Authenticates with Workload Identity
- Deploys and schedules the job

---

###  7. `lead_match_job_deployment.yml`
Deploys the Lead Match Cloud Run job.

**Triggered By:**
- `workflow_call` from `lead_mgmt_image.yml`

**Features:**
- Reads container from Artifact Registry
- Deploys Cloud Run job
- Configurable via input parameters

---

##  Notes

- **Environment Detection:** Some workflows determine the environment dynamically based on Git refs (branches or tags).
- **Service Accounts:** Each environment has its dedicated GCP Workload Identity service account.
- **Reusable Workflows:** `workflow_call` is used for triggering job deployments programmatically.
