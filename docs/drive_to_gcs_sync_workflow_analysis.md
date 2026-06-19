# Google Drive to GCS Synchronization: Workflow & Script Analysis

This document provides a comprehensive technical analysis of the **`sync_drive_with_gcs`** GitHub Actions workflow and its underlying Cloud Run containerized Python execution module (`drive_to_gcs.py`).

---

## 1. Workflow Architecture & Lifecycle

The following Mermaid diagram outlines the end-to-end execution flow—from manual trigger to downstream Dataflow execution.

```mermaid
sequence_graph
graph TD
    A[GitHub Actions UI] -->|workflow_dispatch| B(OIDC WIF Auth to GCP)
    B --> C(gcloud run jobs execute 'snow-sync-job')
    C --> D[Cloud Run Container]
    D -->|1. list_files| E(Google Drive Folder)
    D -->|2. download_file / export_media| E
    D -->|3. upload_to_gcs| F(GCS POS Raw Bucket)
    D -->|4. move_to_archive| E
    D -->|5. upload_manifest| F
    F -->|Eventarc Object Finalize| G(pos_ingestion_workflow)
    G -->|Run Job| H(Dataflow Flex Template)
    H -->|Bulk Ingest| I(Cloud SQL transaction Table)
```

---

## 2. Workflow Basics & Inputs

*   **Workflow Name**: `drive_to_gcs_sync` (defined in `.github/workflows/sync_drive_with_gcs.yml`)
*   **Trigger Type**: `workflow_dispatch` (Manual only; no automated code-push triggers).
*   **Target Environments**: `['adt', 'qat', 'spt', 'prd']` (Default: `adt`).
*   **Branch Behavior**: Can be run against any checked-out branch.

### Manual Inputs Catalog

| Input Name | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `environment` | `choice` | **Yes** | `adt` | The target GCP environment for execution. |
| `drive_folder_id` | `string` | No | `""` | Optional Google Drive folder ID override. If left blank, defaults to the folder configured on the job definition. |

---

## 3. GitHub and Google Cloud Authentication

The workflow leverages a password-less federation standard (**OpenID Connect**) to authorize GitHub Actions within Google Cloud Platform, using target-environment service accounts.

### OIDC & IAM Configurations

| Environment | GCP Project ID | Service Account Email | Secrets Referenced |
| :--- | :--- | :--- | :--- |
| **`adt`** | `p-601-np-bcleadsmgmt-adt` | `secrets.GCP_WORKLOAD_IDENTITY_SA_EMAIL` | `secrets.GCP_WORKLOAD_IDENTITY_PROVIDER_ID` |
| **`qat`** | `p-601-np-bcleadsmgmt-qat` | `secrets.GCP_WORKLOAD_IDENTITY_SA_EMAIL` | `secrets.GCP_WORKLOAD_IDENTITY_PROVIDER_ID` |
| **`spt`** | `p-601-np-bcleadsmgmt-spt` | `secrets.GCP_WORKLOAD_IDENTITY_SA_EMAIL` | `secrets.GCP_WORKLOAD_IDENTITY_PROVIDER_ID` |
| **`prd`** | `p-601-pd-bcleadsmgmt-prd` | `secrets.GCP_WORKLOAD_IDENTITY_SA_EMAIL_PRD` | `secrets.GCP_WORKLOAD_IDENTITY_PROVIDER_ID` |

### Required Workflow Permissions
```yaml
permissions:
  id-token: write  # Crucial to exchange OIDC JWT with Google STS
  contents: read   # Standard checkout permission
```

---

## 4. Google Drive Integration (`drive_to_gcs.py`)

The actual execution of Google Drive downloads occurs inside the Python codebase (`lead_match_codebase/src/costco/leadmgmt/util/drive_to_gcs.py`) running in a Cloud Run Container.

### Operational Features

1.  **File Selection**:
    Queries the Drive API v3 to list all non-trashed, non-folder children in the configured `DRIVE_FOLDER_ID`:
    ```python
    query = f"'{FOLDER_ID}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
    ```
2.  **Native Workspace Exporting**:
    If a file is a native Google Workspace type (such as a Google Spreadsheet), the code automatically maps the mimeType and exports it via `export_media()` to Microsoft Office format (converting sheets to `.xlsx` Excel spreadsheets):
    ```python
    EXPORTABLE = {
        "application/vnd.google-apps.spreadsheet": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"
        ),
        # ... document and presentation conversions
    }
    ```
    For normal binary assets, it executes a direct stream download using `get_media()`.
3.  **Archival & Move Patterns**:
    Once a file has been successfully uploaded to GCS, the script dynamically creates a date-stamped folder inside the same source directory:
    `<DRIVE_FOLDER_ID>/archive_YYYYMMDD_HHMMSS/`
    And moves the processed files into it. This removes them from the parent directory to prevent duplicate ingestions during subsequent runs.
4.  **Resumability and Checkpointing**:
    If a job fails midway, a checkpoint JSON file (`.run_checkpoint.json`) is written to GCS tracking the `run_id` and `archive_folder_id`. On a retry, the script reads this checkpoint to ensure it reuses the same folder and `run_id`, preventing duplicate folder creation and skip-uploading files already landed in GCS.

---

## 5. Google Cloud Storage Outputs

| Output Category | GCS Location Path | Format |
| :--- | :--- | :--- |
| **Raw POS Files** | `gs://<bucket-name>/pos-raw-data/incoming-files/<filename>` | Raw `.xlsx`, `.csv`, `.docx` |
| **Run Checkpoint** | `gs://<bucket-name>/pos-raw-data/incoming-files/.run_checkpoint.json` | JSON |
| **Execution Manifest** | `gs://<bucket-name>/manifests/<run_id>.json` | JSON |

### Manifest JSON Structure
```json
{
  "run_id": "run-20260618-120000",
  "submitted_by": "service-account:gco-iam-svc-lead-mgmt-bc-adt@p-601-np-bcleadsmgmt-adt.iam.gserviceaccount.com",
  "submitted_at": "2026-06-18T12:00:00Z",
  "files": [
    "gs://gcp-gcs-lead-mgmt-us-adt-pos-raw/pos-raw-data/incoming-files/pos_data_20260618.xlsx"
  ]
}
```

---

## 6. Logs, Verification & Diagnostics

### A. Diagnosing Issues via Console Logs
*   **GitHub Actions Log**: Look under the "Trigger Drive → GCS Cloud Run job" step for executing progress metrics.
*   **Cloud Run Job Logs**:
    Go to **Cloud Run Jobs** -> select `snow-sync-job` -> click on **History** -> select your specific execution -> inspect the **Logs** tab. You will see detailed transfer logs:
    ```
    INFO Scanning Drive folder 1A2B3C4D...
    INFO Found 3 file(s) in Drive folder
    INFO Downloading: raw_data.xlsx
    INFO Transferred: raw_data.xlsx → gs://gcp-gcs-.../raw_data.xlsx
    INFO Created archive folder: archive_20260618_120000
    INFO Manifest uploaded: gs://.../manifests/run-20260618-120000.json
    ```

### B. Command-Line Verification

```bash
# 1. List GCS Incoming Bucket to verify POS files landed
gcloud storage ls "gs://gcp-gcs-lead-mgmt-us-adt-pos-raw/pos-raw-data/incoming-files/" \
  --project="p-601-np-bcleadsmgmt-adt"

# 2. List Manifest bucket to verify manifest upload
gcloud storage ls "gs://gcp-gcs-lead-mgmt-us-adt-pos-raw/manifests/" \
  --project="p-601-np-bcleadsmgmt-adt"

# 3. Read manifest contents
gcloud storage cat "gs://gcp-gcs-lead-mgmt-us-adt-pos-raw/manifests/<run_id>.json" \
  --project="p-601-np-bcleadsmgmt-adt"
```

---

## 7. Safety, Downstream Triggers & Gaps

*   **Writes to Database?**: **No**. This workflow does not interface or write records directly to the PostgreSQL database.
*   **Indirect Downstream Triggers**: **Yes, absolutely**. The upload of the manifest JSON to `/manifests/` prefix fires an Eventarc object finalize trigger. This automatically runs `pos_ingestion_workflow` (GCP Workflow), launching the Dataflow Flex Template to load records into Cloud SQL.
*   **Is it Safe to run in ADT?**: **Yes, 100%**. It only operates on the isolated non-production environments and does not touch production GCP nodes or tables.
*   **Operational Risk/Gap**: If the archive phase fails midway after files were copied, a retry uses the checkpoint. However, if any files failed to move to the Drive subfolder, they will remain in the parent folder. This is mitigated by the fact that `drive_to_gcs.py` uses checkpoint markers to skip re-copying existing GCS files.
