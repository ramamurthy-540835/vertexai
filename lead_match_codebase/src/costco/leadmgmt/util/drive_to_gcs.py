"""
Drive → GCS sync job.
Triggered by Cloud Scheduler via Apps Script.

This script:
  1. Checks if the trigger file (defined by TRIGGER_FILENAME env var) exists
     in the Drive folder.
     If NOT found → exits immediately and does nothing.
     If found     → proceeds with the full sync + archive + manifest flow.

  2. Lists all *files* (not sub-folders) in the Drive folder,
     excluding the trigger file.
  3. Downloads & uploads every file to GCS.
  4. Archives all processed files into a date-stamped sub-folder
     created inside the same Drive folder:
     <DRIVE_FOLDER_ID>/archive_YYYYMMDD_HHMMSS/
  5. Creates and uploads a manifest JSON to GCS after all processing
     and archival is complete. The manifest is submitted_by the Cloud Run
     service account identity.
"""

import io
import os
import json
import logging
from datetime import datetime, timezone

import google.auth
from dotenv import load_dotenv
from google.cloud import storage
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from google.auth import default as google_auth_default

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Required env vars ────────────────────────────────────────────────────────
PROJECT_ID  = os.environ["GCP_PROJECT_ID"]
BUCKET_NAME = os.environ["POS_BUCKET_NAME"]
FOLDER_ID   = os.environ["DRIVE_FOLDER_ID"]   # archive subfolder is also created here

# ── Optional env vars ────────────────────────────────────────────────────────
DEST_PREFIX         = os.environ.get("GCS_DESTINATION_PREFIX", "").rstrip("/")
TRIGGER_FILENAME    = os.environ.get("TRIGGER_FILENAME", "process_pos_data.txt")  # comes from env
ARCHIVE_FOLDER_NAME = os.environ.get("ARCHIVE_FOLDER_NAME", "archive")
INCOMING_PREFIX     = os.environ.get("INCOMING_PREFIX", "pos-raw-data/incoming-files")
MANIFESTS_PREFIX    = os.environ.get("MANIFESTS_PREFIX", "manifests")
RUN_LABEL           = os.environ.get("RUN_LABEL", "")   # optional label for manifest run_id

# ── Google Workspace → Office export map ─────────────────────────────────────
EXPORTABLE = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
}

FOLDER_MIME = "application/vnd.google-apps.folder"


# ── Auth helpers ──────────────────────────────────────────────────────────────

def get_service_account_email() -> str:
    """
    Resolve the Cloud Run service account email from ADC credentials.
    Falls back to 'unknown-service-account' if it cannot be determined.
    """
    try:
        credentials, _ = google.auth.default()
        # service_account_email is available on service account credentials
        email = getattr(credentials, "service_account_email", None)
        if email:
            return email
        # For impersonated or other credential types, try .signer_email
        email = getattr(credentials, "signer_email", None)
        if email:
            return email
    except Exception as exc:
        log.warning("Could not resolve service account email: %s", exc)
    return "unknown-service-account"


# ── Drive helpers ─────────────────────────────────────────────────────────────

def build_drive_service():
    creds, _ = google_auth_default(
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def check_trigger_file_exists(drive) -> str | None:
    """
    Check if the trigger file (TRIGGER_FILENAME) exists in FOLDER_ID.
    Returns the file ID if found, None otherwise.
    """
    query = (
        f"'{FOLDER_ID}' in parents "
        f"and trashed = false "
        f"and name = '{TRIGGER_FILENAME}'"
    )
    resp = drive.files().list(
        q=query,
        fields="files(id, name)",
        pageSize=1,
    ).execute()
    files = resp.get("files", [])
    if files:
        log.info(
            "Trigger file '%s' found (id=%s). Proceeding with sync.",
            TRIGGER_FILENAME, files[0]["id"],
        )
        return files[0]["id"]
    log.info(
        "Trigger file '%s' NOT found in folder %s. Nothing to do.",
        TRIGGER_FILENAME, FOLDER_ID,
    )
    return None


def delete_trigger_file(drive, file_id: str) -> None:
    """
    Permanently delete the trigger file from Drive so the next scheduled
    run does not re-trigger the sync.
    """
    drive.files().delete(fileId=file_id).execute()
    log.info("Trigger file '%s' (id=%s) deleted from Drive.", TRIGGER_FILENAME, file_id)


def list_files(drive) -> list[dict]:
    """
    List all non-trashed files (not folders, not trigger file) in FOLDER_ID.
    """
    query = (
        f"'{FOLDER_ID}' in parents "
        f"and trashed = false "
        f"and mimeType != '{FOLDER_MIME}' "
        f"and name != '{TRIGGER_FILENAME}'"    # exclude trigger file at query level
    )
    results, page_token = [], None
    while True:
        resp = drive.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
            pageToken=page_token,
            pageSize=100,
        ).execute()
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results


def create_archive_folder(drive) -> str:
    """
    Create a date-stamped sub-folder inside FOLDER_ID and return its ID.
    Name format: <ARCHIVE_FOLDER_NAME>_YYYYMMDD_HHMMSS (UTC)
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    folder_name = f"{ARCHIVE_FOLDER_NAME}_{timestamp}"
    metadata = {
        "name": folder_name,
        "mimeType": FOLDER_MIME,
        "parents": [FOLDER_ID],
    }
    folder = drive.files().create(body=metadata, fields="id, name").execute()
    log.info("Created archive folder: %s (id=%s)", folder["name"], folder["id"])
    return folder["id"]


def move_to_archive(drive, file_id: str, archive_folder_id: str) -> None:
    """
    Move a file into the archive subfolder.
    Uses Drive v3 move pattern: add new parent, remove old parent.
    """
    drive.files().update(
        fileId=file_id,
        addParents=archive_folder_id,
        removeParents=FOLDER_ID,
        fields="id, parents",
    ).execute()


# ── GCS helpers ───────────────────────────────────────────────────────────────

def gcs_blob_name(filename: str) -> str:
    prefix = f"{DEST_PREFIX}/{INCOMING_PREFIX}" if DEST_PREFIX else INCOMING_PREFIX
    return f"{prefix}/{filename}"


def already_in_gcs(gcs: storage.Client, filename: str) -> bool:
    return gcs.bucket(BUCKET_NAME).blob(gcs_blob_name(filename)).exists()


def resolve_final_name(file: dict) -> str:
    """Append the correct extension for Google Workspace exports."""
    name = file["name"]
    mime = file["mimeType"]
    if mime in EXPORTABLE:
        _, ext = EXPORTABLE[mime]
        if not name.endswith(ext):
            name += ext
    return name


def download_file(drive, file: dict) -> bytes:
    mime = file["mimeType"]
    if mime in EXPORTABLE:
        export_mime, _ = EXPORTABLE[mime]
        request = drive.files().export_media(fileId=file["id"], mimeType=export_mime)
    else:
        request = drive.files().get_media(fileId=file["id"])
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request, chunksize=8 * 1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def upload_to_gcs(gcs: storage.Client, data: bytes, filename: str) -> str:
    blob_name = gcs_blob_name(filename)
    gcs.bucket(BUCKET_NAME).blob(blob_name).upload_from_string(data)
    return f"gs://{BUCKET_NAME}/{blob_name}"


# ── Manifest helpers ──────────────────────────────────────────────────────────

def build_run_id() -> str:
    """
    Build a run_id from RUN_LABEL env var (if set) or auto-generate one.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if RUN_LABEL:
        clean = "".join(c if c.isalnum() or c == "-" else "-" for c in RUN_LABEL.lower())
        return f"{clean}-{timestamp}"
    return f"run-{timestamp}"


def build_manifest(run_id: str, gcs_paths: list[str], service_account_email: str) -> dict:
    """
    Build the manifest JSON.
    submitted_by reflects the Cloud Run service account, not a GitHub actor.
    """
    return {
        "run_id": run_id,
        "submitted_by": f"service-account:{service_account_email}",
        "submitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trigger_file": TRIGGER_FILENAME,
        "files": gcs_paths,
    }


def upload_manifest(gcs: storage.Client, manifest: dict, run_id: str) -> str:
    """
    Upload the manifest JSON to GCS under MANIFESTS_PREFIX/<run_id>.json.
    Returns the gs:// URI of the uploaded manifest.
    """
    blob_name = f"{MANIFESTS_PREFIX}/{run_id}.json"
    data = json.dumps(manifest, indent=2).encode("utf-8")
    gcs.bucket(BUCKET_NAME).blob(blob_name).upload_from_string(
        data, content_type="application/json"
    )
    uri = f"gs://{BUCKET_NAME}/{blob_name}"
    log.info("Manifest uploaded: %s", uri)
    return uri


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    drive = build_drive_service()
    gcs   = storage.Client(project=PROJECT_ID)

    # Resolve service account identity once upfront for use in manifest
    service_account_email = get_service_account_email()
    log.info("Running as service account: %s", service_account_email)

    # ── STEP 1: Check for trigger file — exit early if not present ────────────
    log.info(
        "Checking for trigger file '%s' in Drive folder %s …",
        TRIGGER_FILENAME, FOLDER_ID,
    )
    trigger_file_id = check_trigger_file_exists(drive)
    if not trigger_file_id:
        log.info("No trigger file found. Exiting with no action.")
        return                          # clean exit — nothing to do

    # ── STEP 2: List files to process ────────────────────────────────────────
    log.info("Scanning Drive folder %s (trigger file excluded)", FOLDER_ID)
    files = list_files(drive)
    log.info("Found %d file(s) to process", len(files))

    if not files:
        log.info("No files to process. Exiting.")
        return

    # ── STEP 3: Sync — download from Drive and upload to GCS ─────────────────
    transferred = skipped = failed = 0
    transferred_gcs_paths = []         # collect gs:// paths for manifest

    for file in files:
        fname = file["name"]
        try:
            final_name = resolve_final_name(file)

            if already_in_gcs(gcs, final_name):
                log.info("SKIP (already in GCS): %s", final_name)
                skipped += 1
                continue

            log.info("Downloading: %s (%s)", fname, file["mimeType"])
            data = download_file(drive, file)
            gcs_path = upload_to_gcs(gcs, data, final_name)
            log.info("Transferred: %s → %s", fname, gcs_path)
            transferred_gcs_paths.append(gcs_path)
            transferred += 1

        except Exception as exc:
            log.error("FAILED: %s — %s", fname, exc)
            failed += 1

    log.info(
        "Sync done. transferred=%d  skipped=%d  failed=%d",
        transferred, skipped, failed,
    )

    # ── STEP 4: Archive — move all processed files into dated sub-folder ──────
    log.info(
        "Archiving %d file(s) into dated sub-folder inside folder %s …",
        len(files), FOLDER_ID,
    )
    archive_folder_id = create_archive_folder(drive)

    archived = archive_failed = 0
    for file in files:
        try:
            move_to_archive(drive, file["id"], archive_folder_id)
            log.info("Archived: %s", file["name"])
            archived += 1
        except HttpError as exc:
            log.error("ARCHIVE FAILED: %s — %s", file["name"], exc)
            archive_failed += 1

    log.info("Archive done. archived=%d  failed=%d", archived, archive_failed)

    # ── STEP 5: Delete trigger file — prevents re-triggering on next run ──────
    try:
        delete_trigger_file(drive, trigger_file_id)
    except HttpError as exc:
        log.error("TRIGGER FILE DELETE FAILED: %s", exc)
        # Non-fatal: log and continue — manifest should still be created

    # ── STEP 6: Manifest — only runs after sync + archive + delete complete ───
    log.info("Building and uploading manifest …")
    run_id   = build_run_id()
    manifest = build_manifest(run_id, transferred_gcs_paths, service_account_email)
    log.info("Manifest contents:\n%s", json.dumps(manifest, indent=2))

    try:
        manifest_uri = upload_manifest(gcs, manifest, run_id)
        log.info(
            "Manifest creation complete. run_id=%s  files=%d  uri=%s",
            run_id, len(transferred_gcs_paths), manifest_uri,
        )
    except Exception as exc:
        log.error("MANIFEST UPLOAD FAILED: %s", exc)
        raise SystemExit(1)

    # ── Final exit code ───────────────────────────────────────────────────────
    if failed or archive_failed:
        raise SystemExit(1)


if __name__ == "__main__":
    run()