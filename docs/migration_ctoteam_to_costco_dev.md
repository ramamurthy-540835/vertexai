# Costco Dev Lead-to-POS Pipeline Checklist

## Overview

This document covers every task required to stand up and operate the Lead Match pipeline in a Costco development GCP project. Treat `ctoteam` as a historical sandbox only. Costco Dev must use its own project, bucket, Cloud SQL instance, service accounts, secrets, and container images.

The matching model is exact-first:

1. Run and persist the exact/deterministic match layer.
2. Validate which leads and POS rows were already claimed by exact matching.
3. Run semantic fuzzy matching only on the residual records that did not qualify in exact matching.
4. Generate reports from the combined exact + fuzzy result set.

The Cloud Run fuzzy layer has exact-match exclusion guards. Those guards only work if exact results are persisted in Cloud SQL before fuzzy starts.

---

## Costco Activity List After Exact Matching

Use this list after Costco source data is loaded and the exact matching layer has run.

| # | Activity | Owner | Expected Output |
|---|----------|-------|-----------------|
| 1 | Run exact/deterministic matching for the target warehouse scope | Costco matching/data team | Exact matches identified with lead/POS IDs, score, and match type |
| 2 | Persist exact results to Cloud SQL before fuzzy starts | Costco matching/data team | Exact records visible in `leadmgmt.transaction` and/or `leadmgmt.match_decision_detail` |
| 3 | Validate exact result counts | Costco matching/data team | Counts by `match_type`, exact-qualified leads, exact-qualified POS |
| 4 | Validate residual population | Costco matching/data team | Count of leads and POS still eligible for fuzzy |
| 5 | Run semantic workflow in dry-run mode | Platform/GitHub Actions operator | Fuzzy logs `would_insert_rows`; no fuzzy rows are written |
| 6 | Review fuzzy dry-run sample | Business/data reviewer | Confirm fuzzy candidates are valid secondary matches |
| 7 | Run semantic workflow with `dry_run=false` | Platform/GitHub Actions operator | Fuzzy rows written only for residual records |
| 8 | Generate and review final reports | Business/data reviewer | Exact + fuzzy coverage, manual review queue, unmatched population |

Exact persistence contract:

- If exact writes to `leadmgmt.transaction`, set `lead_id`, `match_type`, and `match_score`.
- If exact writes to `leadmgmt.match_decision_detail`, set `lead_id`, `pos_id`, `match_type`, and `final_score`.
- Exact-qualified `match_type` values should align with the runtime default list: `Exact`, `Deterministic`, `Exact Match`, `Direct Match`, `Close Match`.
- Exact qualification score defaults to `80`; override with `EXACT_MATCH_MIN_SCORE` only if Costco business rules require a different threshold.

Fuzzy skips exact-qualified leads and POS rows in two places:

- embedding generation, so newly embedded fuzzy inputs exclude exact-claimed records
- fuzzy candidate selection, so previously embedded exact-claimed records are not reassigned

---

## Phase 0: GCP APIs to Enable

Enable these services in the target project before anything else.

| # | GCP API | Service | Why |
|---|---------|---------|-----|
| 1 | `aiplatform.googleapis.com` | Vertex AI | Gemini embedding generation (gemini-embedding-001) + Gemini 2.5 Flash for reporting AI |
| 2 | `sqladmin.googleapis.com` | Cloud SQL Admin | PostgreSQL + pgvector instance management |
| 3 | `run.googleapis.com` | Cloud Run | 5 batch jobs + 2 web services |
| 4 | `workflows.googleapis.com` | Cloud Workflows | Pipeline orchestration |
| 5 | `storage.googleapis.com` | Cloud Storage | Reports, preflight contracts, monitoring snapshots |
| 6 | `cloudbuild.googleapis.com` | Cloud Build | Docker image builds |
| 7 | `artifactregistry.googleapis.com` | Artifact Registry | Container image hosting |
| 8 | `secretmanager.googleapis.com` | Secret Manager | DB password storage |
| 9 | `iam.googleapis.com` | IAM | Service account management |
| 10 | `sts.googleapis.com` | Security Token Service | Workload Identity Federation (GitHub Actions keyless auth) |
| 11 | `vpcaccess.googleapis.com` | VPC Access | If Cloud SQL uses private IP (Serverless VPC Connector for Cloud Run) |

```bash
gcloud services enable \
  aiplatform.googleapis.com \
  sqladmin.googleapis.com \
  run.googleapis.com \
  workflows.googleapis.com \
  storage.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  sts.googleapis.com \
  vpcaccess.googleapis.com \
  --project=<COSTCO_PROJECT_ID>
```

---

## Phase 1: Infrastructure Setup (One-Time)

### 1.1 Artifact Registry Repository

```bash
gcloud artifacts repositories create cloud-run-repo \
  --repository-format=docker \
  --location=us-central1 \
  --project=<COSTCO_PROJECT_ID>
```

### 1.2 GCS Bucket

```bash
gsutil mb -p <COSTCO_PROJECT_ID> -l us-central1 gs://lead-match-<COSTCO_PROJECT_ID>
```

### 1.3 Secret Manager — DB Password

```bash
echo -n "<DB_PASSWORD_VALUE>" | gcloud secrets create lead-match-db-password \
  --data-file=- \
  --project=<COSTCO_PROJECT_ID>
```

### 1.4 Cloud SQL Instance

Create a PostgreSQL 15 instance with pgvector support:

```bash
gcloud sql instances create lead-mgmt-db \
  --database-version=POSTGRES_15 \
  --tier=db-custom-4-15360 \
  --region=us-central1 \
  --storage-size=100GB \
  --storage-auto-increase \
  --availability-type=regional \
  --project=<COSTCO_PROJECT_ID>
```

### 1.5 Database Schema Setup

Connect to the Cloud SQL instance and run:

```sql
-- Extensions (must be created by superuser)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Schema
CREATE SCHEMA IF NOT EXISTS leadmgmt;
```

Then create all 13 tables. Use `schema/ctoteam_cloudsql_leadmgmt_schema_from_excel.sql`; the file name is historical, but the schema is the current Lead Match Cloud SQL schema.

| # | Table | Purpose |
|---|-------|---------|
| 1 | `account` | Corporate business account profiles |
| 2 | `lead` | Active sales/marketing leads from ServiceNow |
| 3 | `contact` | Primary contacts attached to leads |
| 4 | `pos_transactions` | POS purchase records (legacy format) |
| 5 | `transaction` | Full POS transaction table with OMS columns (primary) |
| 6 | `batch_audit` | Staging ingestion tracking |
| 7 | `match_audit` | Daily matching statistics |
| 8 | `error_audit` | Pipeline failure logs |
| 9 | `api_audit` | API sync request tracking |
| 10 | `leads_embeddings` | 768-dim semantic vectors for leads (pgvector VECTOR(768)) |
| 11 | `pos_embeddings` | 768-dim semantic vectors for POS transactions |
| 12 | `match_configuration` | Score-to-confidence threshold mappings |
| 13 | `match_decision_detail` | Fuzzy match results with explainability |

Plus: `transaction_pos_id_seq` sequence (starts at 168029).

Seed `match_configuration`:
```sql
INSERT INTO leadmgmt.match_configuration (confidence_level, min_score, max_score, match_result) VALUES
  ('High', 90, 100, 'Match'),
  ('Medium', 85, 89.999, 'Potential'),
  ('Low', 80, 84.999, 'Potential'),
  ('No Match', 0, 79.999, 'No Match');
```

### 1.6 HNSW Indexes

Run `schema/lead_match_hnsw_indexes.sql` to create:
- `idx_leads_embeddings_combined_hnsw` (HNSW, cosine, m=16, ef_construction=128)
- `idx_pos_embeddings_combined_hnsw` (HNSW, cosine, m=16, ef_construction=128)

---

## Phase 2: Data Loading

### 2.1 Load Source Data

Data must be populated **before** the matching pipeline runs. Load order matters due to foreign keys.

| Step | Table | Source | Tool |
|------|-------|--------|------|
| 1 | `account` | `leads_corrected.xlsx` | `mock_data/load_mock_data.py` |
| 2 | `lead` | `leads_corrected.xlsx` | `mock_data/load_mock_data.py` |
| 3 | `contact` | `leads_corrected.xlsx` | `mock_data/load_mock_data.py` |
| 4 | `pos_transactions` | `pos_corrected.xlsx` | `mock_data/load_mock_data.py` |
| 5 | `transaction` | `pos_corrected.xlsx` | `mock_data/load_mock_data.py` |

Data files go under `mock_data/<warehouse_number>/` (e.g., `mock_data/569/`).

### 2.2 Validate Data Loaded

```sql
SELECT 'account' AS tbl, COUNT(*) FROM leadmgmt.account
UNION ALL SELECT 'lead', COUNT(*) FROM leadmgmt.lead
UNION ALL SELECT 'contact', COUNT(*) FROM leadmgmt.contact
UNION ALL SELECT 'pos_transactions', COUNT(*) FROM leadmgmt.pos_transactions
UNION ALL SELECT 'transaction', COUNT(*) FROM leadmgmt.transaction;
```

### 2.3 Run Exact Matching Before Fuzzy

Exact/deterministic matching is the primary attribution layer. Costco must run this layer before the semantic workflow writes fuzzy results.

Minimum exact output fields:

| Field | Required Why |
|-------|--------------|
| `lead_id` | Identifies the lead claimed by exact matching |
| `pos_id` | Identifies the POS row claimed by exact matching |
| `match_type` | Must identify the result as exact/deterministic/close/direct |
| `match_score` or `final_score` | Used to decide if the exact result qualifies |
| `warehouse_number` | Keeps attribution warehouse-scoped |

Recommended validation queries:

```sql
SELECT match_type, COUNT(*)
FROM leadmgmt.transaction
WHERE match_type IS NOT NULL
GROUP BY match_type
ORDER BY COUNT(*) DESC;

SELECT match_type, COUNT(*)
FROM leadmgmt.match_decision_detail
WHERE match_type IS NOT NULL
GROUP BY match_type
ORDER BY COUNT(*) DESC;
```

### 2.4 Validate Fuzzy Residual Scope

Before fuzzy, estimate the population that remains eligible after exact matching:

```sql
WITH exact_leads AS (
  SELECT DISTINCT lead_id
  FROM leadmgmt.match_decision_detail
  WHERE lower(match_type) IN ('exact', 'deterministic', 'exact match', 'direct match', 'close match')
    AND (final_score IS NULL OR final_score >= 80)
  UNION
  SELECT DISTINCT lead_id
  FROM leadmgmt.transaction
  WHERE lead_id IS NOT NULL
    AND lower(match_type) IN ('exact', 'deterministic', 'exact match', 'direct match', 'close match')
    AND (match_score IS NULL OR match_score >= 80)
),
exact_pos AS (
  SELECT DISTINCT pos_id
  FROM leadmgmt.match_decision_detail
  WHERE lower(match_type) IN ('exact', 'deterministic', 'exact match', 'direct match', 'close match')
    AND (final_score IS NULL OR final_score >= 80)
  UNION
  SELECT DISTINCT pos_id
  FROM leadmgmt.transaction
  WHERE lower(match_type) IN ('exact', 'deterministic', 'exact match', 'direct match', 'close match')
    AND (match_score IS NULL OR match_score >= 80)
)
SELECT 'fuzzy_eligible_leads' AS metric, COUNT(*)
FROM leadmgmt.lead l
WHERE NOT EXISTS (SELECT 1 FROM exact_leads e WHERE e.lead_id = l.lead_id)
UNION ALL
SELECT 'fuzzy_eligible_pos' AS metric, COUNT(*)
FROM leadmgmt.transaction t
WHERE NOT EXISTS (SELECT 1 FROM exact_pos e WHERE e.pos_id = t.pos_id);
```

---

## Phase 3: IAM & Service Accounts

### 3.1 Create the GitHub Actions Service Account

```bash
gcloud iam service-accounts create github-actions-lead-match \
  --display-name="GitHub Actions Lead Match" \
  --project=<COSTCO_PROJECT_ID>
```

### 3.2 Grant Required Roles

```bash
SA="serviceAccount:github-actions-lead-match@<COSTCO_PROJECT_ID>.iam.gserviceaccount.com"
PROJECT=<COSTCO_PROJECT_ID>

for ROLE in \
  roles/aiplatform.user \
  roles/artifactregistry.writer \
  roles/cloudbuild.builds.editor \
  roles/cloudsql.viewer \
  roles/iam.serviceAccountUser \
  roles/run.admin \
  roles/storage.admin \
  roles/workflows.editor \
  roles/workflows.invoker \
  roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="$SA" --role="$ROLE"
done
```

> Note: `roles/run.viewer`, `roles/workflows.viewer`, `roles/storage.objectAdmin` are excluded (redundant with admin roles above).

### 3.3 Workload Identity Federation

Set up WIF pool + provider for GitHub Actions OIDC:

```bash
# Create pool
gcloud iam workload-identity-pools create github-actions \
  --location=global \
  --project=<COSTCO_PROJECT_ID>

# Create provider
gcloud iam workload-identity-pools providers create-oidc github \
  --workload-identity-pool=github-actions \
  --location=global \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --project=<COSTCO_PROJECT_ID>

# Bind SA to WIF
gcloud iam service-accounts add-iam-policy-binding \
  github-actions-lead-match@<COSTCO_PROJECT_ID>.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github-actions/attribute.repository/<GITHUB_ORG>/<REPO>" \
  --project=<COSTCO_PROJECT_ID>
```

### 3.4 GitHub Secrets to Configure

| Secret Name | Value |
|-------------|-------|
| `GCP_WORKLOAD_IDENTITY_PROVIDER_ID` | `projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github-actions/providers/github` |
| `GCP_WORKLOAD_IDENTITY_SA_EMAIL` | `github-actions-lead-match@<COSTCO_PROJECT_ID>.iam.gserviceaccount.com` |

---

## Phase 4: Create Cloud Run Jobs (5 jobs)

All 5 jobs share the same container image. They must be **created first** (with DB connector + secrets) before the semantic workflow can update them.

```bash
PROJECT=<COSTCO_PROJECT_ID>
REGION=us-central1
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/cloud-run-repo/lead-match-runtime:latest"
CONN="${PROJECT}:${REGION}:lead-mgmt-db"

for JOB in \
  lead-match-ensure-indexes \
  lead-match-lead-embeddings \
  lead-match-pos-embeddings \
  lead-match-fuzzy-match \
  lead-match-report; do

  gcloud run jobs create "${JOB}" \
    --project="${PROJECT}" \
    --region="${REGION}" \
    --image="${IMAGE}" \
    --add-cloudsql-instances="${CONN}" \
    --set-secrets="DB_PASSWORD=lead-match-db-password:latest" \
    --set-env-vars="DB_NAME=postgres,DB_SCHEMA=leadmgmt,DB_USER=postgres,VERTEX_PROJECT_ID=${PROJECT},GOOGLE_CLOUD_PROJECT=${PROJECT},VERTEX_LOCATION=${REGION},CLOUDSQL_CONNECTION_NAME=${CONN},CLOUDSQL_SOCKET_DIR=/cloudsql,ALLOW_CLIENT_GCP=false,ALLOW_PRODUCTION=false,EXPECTED_PROJECT_ID=${PROJECT},WAREHOUSE_SCOPE=ALL,DEFAULT_FISCAL_YEAR=2026,DEFAULT_FISCAL_PERIOD=10,MATCH_MIN_SCORE=78,EMBEDDING_BATCH_SIZE=100,EMBEDDING_MAX_WORKERS=3,EMBEDDING_BATCH_WORKERS=3,EMBEDDING_MAX_RETRIES=6,EMBEDDING_REQUEST_LOG_EVERY=100,MATCH_BATCH_SIZE=100,MATCH_STATEMENT_TIMEOUT_MS=900000,HNSW_EF_SEARCH=100" \
    --task-timeout=3600 \
    --max-retries=0 \
    --cpu=2 \
    --memory=4Gi
done
```

> Note: You must build and push the runtime image first (Phase 5, or manually via `gcloud builds submit`).

---

## Phase 5: Pipeline Execution Order

### Step 1: Build the Runtime Image

Either manually or via the semantic workflow with `deploy_runtime=true`:

```bash
gcloud builds submit \
  --project=<COSTCO_PROJECT_ID> \
  --config=cloudbuild.lead-match.yaml \
  --substitutions=SHORT_SHA=$(git rev-parse --short HEAD) \
  .
```

### Step 2: Run Preflight (Optional but Recommended)

Trigger `lead_match_preflight_ops.yml` with:
- `warehouse`: target warehouse(s)
- `prepare_resources`: `true`
- `run_smoke_tests`: `true`
- `run_cloud_sql_index_maintenance`: `true`

This validates infrastructure and creates the `READY` gate file in GCS.

### Step 3: Run the Semantic Workflow (Main Pipeline)

Run this only after exact results are persisted and residual counts are validated.

Trigger `lead_match_semantic_workflow.yml` with:
- `warehouse`: e.g., `569`
- `deploy_runtime`: `true`
- `update_cloud_run_jobs`: `true`
- `deploy_workflow`: `true`
- `prepare_readiness`: `true` (or `false` if preflight already ran)
- `wait_for_completion`: `true`

This executes in order:
1. Build runtime image (Cloud Build)
2. Update all 5 Cloud Run Jobs with new image
3. Deploy `lead_match_workflow.yaml` to GCP Workflows
4. Check/create READY gate
5. **Trigger the GCP Cloud Workflow**, which runs:
   - a. Lead Embeddings + POS Embeddings (parallel)
   - b. Ensure HNSW indexes (after embeddings)
   - c. Fuzzy Match (after indexes)
   - d. Report generation (after match)

For the first Costco run after exact matching, use dry-run controls:

| Input | Value |
|-------|-------|
| `warehouse` | target warehouse, for example `115` or `569` |
| `deploy_runtime` | `true` |
| `update_cloud_run_jobs` | `true` |
| `deploy_workflow` | `true` |
| `prepare_readiness` | `true` |
| `wait_for_completion` | `true` |
| `dry_run` | `true` |
| `run_limits` | `lead=50,pos=500,match=25` |

Dry run writes a capped preview of up to 10 fuzzy rows into `match_decision_detail`, writes a `DRY_RUN` row into `match_audit`, and uploads `dryrun_*` report files. It does not update business-state fields in `lead`, `transaction`, or `pos_transactions` by default.

After Costco approves the dry-run sample, run again with:

| Input | Value |
|-------|-------|
| `dry_run` | `false` |
| `run_limits` | blank or `all` |

### Step 4: Deploy Web Apps (Post-Pipeline)

```bash
# Monitoring App
gh workflow run lead_match_monitoring_app.yml

# Reporting App
gh workflow run lead_match_reporting_app.yml
```

### Ongoing: Monitor

`lead_match_monitor.yml` runs automatically every 30 minutes via cron. No manual action required.

---

## Phase 6: Configuration Changes Required

Files that contain historical sandbox references to update for Costco Dev:

| File | What to Change |
|------|---------------|
| `.github/workflows/lead_match_semantic_workflow.yml` | `PROJECT_ID: ctoteam`, `CLOUDSQL_CONNECTION_NAME`, bucket name |
| `.github/workflows/lead_match_preflight_ops.yml` | `PROJECT_ID: ctoteam`, `CLOUDSQL_CONNECTION_NAME` |
| `.github/workflows/lead_match_monitor.yml` | `PROJECT_ID: ctoteam` |
| `.github/workflows/lead_match_monitoring_app.yml` | `PROJECT_ID: ctoteam` |
| `.github/workflows/lead_match_reporting_app.yml` | `PROJECT_ID: ctoteam` |
| `deploy/lead_match_workflow.yaml` | Default project/region references |
| `lead_match_runtime/lead_to_pos_match_rules.json` | If project-specific values exist |
| GCS bucket name | `lead-match-ctoteam` → `lead-match-<COSTCO_PROJECT_ID>` |

---

## Phase 7: Validation Checklist

| # | Check | How |
|---|-------|-----|
| 1 | All 11 APIs enabled | `gcloud services list --enabled --project=<PROJECT>` |
| 2 | Cloud SQL instance running | `gcloud sql instances describe lead-mgmt-db` |
| 3 | pgvector extension installed | `SELECT * FROM pg_extension WHERE extname = 'vector';` |
| 4 | All 13 tables exist | `SELECT table_name FROM information_schema.tables WHERE table_schema = 'leadmgmt';` |
| 5 | Source data loaded | Row counts for account, lead, contact, pos_transactions, transaction |
| 6 | Artifact Registry repo exists | `gcloud artifacts repositories list --location=us-central1` |
| 7 | Secret exists | `gcloud secrets describe lead-match-db-password` |
| 8 | Service account has all roles | `gcloud projects get-iam-policy <PROJECT> --flatten=...` |
| 9 | WIF configured | `gcloud iam workload-identity-pools list --location=global` |
| 10 | GitHub secrets set | Check repo Settings → Secrets |
| 11 | 5 Cloud Run Jobs exist | `gcloud run jobs list --region=us-central1` |
| 12 | GCS bucket exists | `gsutil ls gs://lead-match-<PROJECT>` |
| 13 | Runtime image built | `gcloud artifacts docker images list ...` |
| 14 | Smoke test passes | Run `lead_match_runtime/smoke_test.py --warehouse <N>` |
| 15 | Preflight passes | Trigger preflight workflow, check READY marker |
| 16 | Full pipeline completes | Trigger semantic workflow, wait for SUCCEEDED |

---

## Cloud Run Resources Summary

### Jobs (5) — all share `lead-match-runtime` image

| Job Name | Container Args | Purpose |
|----------|---------------|---------|
| `lead-match-ensure-indexes` | `ensure-indexes` | Create/verify pgvector HNSW indexes |
| `lead-match-lead-embeddings` | (default) | Generate Vertex AI embeddings for leads |
| `lead-match-pos-embeddings` | (default) | Generate Vertex AI embeddings for POS |
| `lead-match-fuzzy-match` | (default) | Run semantic fuzzy matching via pgvector |
| `lead-match-report` | (default) | Generate match reports → GCS |

### Services (2) — separate images

| Service Name | Image | Purpose |
|-------------|-------|---------|
| `lead-match-monitoring-app` | `lead-match-monitoring-app` | Next.js monitoring dashboard |
| `lead-match-reporting-app` | `lead-match-reporting-app` | Next.js reporting + AI Q&A |

### Workflows (1)

| Workflow Name | Source File |
|--------------|------------|
| `lead_match_workflow` | `deploy/lead_match_workflow.yaml` |

### GitHub Actions Workflows (5)

| Workflow | Purpose |
|----------|---------|
| `lead_match_preflight_ops.yml` | Infrastructure validation + readiness gate |
| `lead_match_semantic_workflow.yml` | Main pipeline orchestrator |
| `lead_match_monitor.yml` | Ongoing monitoring (cron every 30 min) |
| `lead_match_monitoring_app.yml` | Deploy monitoring web app |
| `lead_match_reporting_app.yml` | Deploy reporting web app |
