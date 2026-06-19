"""
Local test: GCS → Google Drive write test.

Step 1 — Permission check:
    Creates a tiny dummy text file in memory and uploads it to the
    target Drive folder to confirm write access.

Step 2 — Real GCS → Drive transfer:
    Downloads a blob from GCS and uploads it into the same Drive folder.

Auth: uses ADC to impersonate SERVICE_ACCOUNT, which has Drive access to
the corporate Shared Drive. Your personal ADC (c_eyashwanthrahul@costco.com)
is the source; the SA is the target principal.

Required env vars:
    GCP_PROJECT_ID   - GCP project
    POS_BUCKET_NAME  - GCS bucket to read from
    DRIVE_FOLDER_ID  - Drive folder to write INTO (destination)
    SERVICE_ACCOUNT  - SA email to impersonate (must have Drive + GCS access)

Optional env vars:
    GCS_BLOB_NAME    - specific blob path to transfer (e.g. pos-raw-data/incoming-files/myfile.xlsx)
                       if not set, the script lists blobs under GCS_PREFIX and picks the first one
    GCS_PREFIX       - prefix to scan for blobs (default: pos-raw-data/incoming-files)

Run:
    pip install google-auth google-api-python-client google-cloud-storage python-dotenv
    export GCP_PROJECT_ID=...
    export POS_BUCKET_NAME=...
    export DRIVE_FOLDER_ID=...
    export SERVICE_ACCOUNT=...
    python testing_code/test_gcs_to_drive.py
"""

import io
import os
import sys
import logging
from datetime import datetime, timezone

import google.auth
from dotenv import load_dotenv
from google.auth import impersonated_credentials
from google.cloud import storage
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Env vars ──────────────────────────────────────────────────────────────────
PROJECT_ID      = os.environ["GCP_PROJECT_ID"]
BUCKET_NAME     = os.environ["POS_BUCKET_NAME"]
DRIVE_FOLDER_ID = os.environ["DRIVE_FOLDER_ID"]
SERVICE_ACCOUNT = os.environ["SERVICE_ACCOUNT"]
GCS_BLOB_NAME   = os.environ.get("GCS_BLOB_NAME", "")
GCS_PREFIX      = os.environ.get("GCS_PREFIX", "pos-raw-data/incoming-files")

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/devstorage.read_write",
]


# ── Auth: use ADC directly (user account with Drive scope) ────────────────────

def get_credentials():
    credentials, _ = google.auth.default(scopes=DRIVE_SCOPES)
    return credentials


# ── Drive helpers ─────────────────────────────────────────────────────────────

def build_drive_service(credentials):
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def upload_to_drive(drive, data: bytes, filename: str, mime_type: str = "application/octet-stream") -> dict:
    """Upload bytes as a new file in DRIVE_FOLDER_ID. Returns the created file metadata."""
    metadata = {
        "name": filename,
        "parents": [DRIVE_FOLDER_ID],
    }
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=True)
    file = drive.files().create(
        body=metadata,
        media_body=media,
        fields="id, name, webViewLink",
        supportsAllDrives=True,
    ).execute()
    return file


# ── GCS helpers ───────────────────────────────────────────────────────────────

def pick_gcs_blob(gcs_client) -> tuple[str, bytes]:
    """
    Writes a tiny test file to GCS then reads it back.
    Keeps the test fast — no large production files downloaded.
    """
    bucket = gcs_client.bucket(BUCKET_NAME)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    blob_name = f"{GCS_PREFIX}/_test_gcs_write_{timestamp}.txt"
    test_data = f"GCS connectivity test\ntimestamp: {timestamp}\n".encode("utf-8")

    log.info("Writing test blob to gs://%s/%s ...", BUCKET_NAME, blob_name)
    bucket.blob(blob_name).upload_from_string(test_data, content_type="text/plain")
    log.info("GCS write OK — reading back ...")
    data = bucket.blob(blob_name).download_as_bytes()
    log.info("GCS read OK (%d bytes)", len(data))
    bucket.blob(blob_name).delete()
    log.info("GCS test blob deleted")
    return blob_name, data


# ── Test steps ────────────────────────────────────────────────────────────────

def step1_permission_check(drive) -> bool:
    """Upload a tiny dummy text file to Drive. Returns True on success."""
    log.info("=" * 60)
    log.info("STEP 1: Permission check — uploading dummy file to Drive")
    log.info("=" * 60)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dummy_name = f"_test_write_check_{timestamp}.txt"
    dummy_data = (
        f"GCS → Drive write test\n"
        f"timestamp: {timestamp}\n"
        f"service_account: {SERVICE_ACCOUNT}\n"
    ).encode("utf-8")

    try:
        file = upload_to_drive(drive, dummy_data, dummy_name, mime_type="text/plain")
        log.info("SUCCESS: dummy file created in Drive")
        log.info("  File ID  : %s", file["id"])
        log.info("  File name: %s", file["name"])
        log.info("  View URL : %s", file.get("webViewLink", "n/a"))
        return True
    except Exception as exc:
        log.error("FAILED: could not write dummy file to Drive — %s", exc)
        return False


def step2_gcs_to_drive(drive, gcs_client) -> bool:
    """Download a blob from GCS and upload it to Drive. Returns True on success."""
    log.info("=" * 60)
    log.info("STEP 2: Real GCS → Drive transfer")
    log.info("=" * 60)

    blob_name, data = pick_gcs_blob(gcs_client)
    filename = blob_name.split("/")[-1]

    log.info("Downloading gs://%s/%s (%d bytes) ...", BUCKET_NAME, blob_name, len(data))

    # Guess mime type from extension
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime_map = {
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv":  "text/csv",
        "json": "application/json",
        "txt":  "text/plain",
        "pdf":  "application/pdf",
    }
    mime_type = mime_map.get(ext, "application/octet-stream")

    try:
        file = upload_to_drive(drive, data, filename, mime_type=mime_type)
        log.info("SUCCESS: file uploaded to Drive")
        log.info("  GCS source : gs://%s/%s", BUCKET_NAME, blob_name)
        log.info("  Drive file : %s  (id=%s)", file["name"], file["id"])
        log.info("  View URL   : %s", file.get("webViewLink", "n/a"))
        return True
    except Exception as exc:
        log.error("FAILED: could not upload file to Drive — %s", exc)
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("Running as: %s", SERVICE_ACCOUNT)
    log.info("Target Drive folder: %s", DRIVE_FOLDER_ID)
    log.info("Source GCS bucket  : gs://%s", BUCKET_NAME)

    credentials = get_credentials()
    drive       = build_drive_service(credentials)
    gcs_client  = storage.Client(project=PROJECT_ID, credentials=credentials)

    ok1 = step1_permission_check(drive)
    if not ok1:
        log.error("Stopping — permission check failed. Fix Drive write access first.")
        sys.exit(1)

    ok2 = step2_gcs_to_drive(drive, gcs_client)
    if not ok2:
        sys.exit(1)

    log.info("All steps passed.")


if __name__ == "__main__":
    main()
