"""
Drive → GCS sync job.
Manually triggered via Cloud Run Job.

This script:
  1. Lists all *files* (not sub-folders) in the Drive folder.
  2. Downloads & uploads every file to GCS.
  3. Archives all processed files into a date-stamped sub-folder
     created inside the same Drive folder:
     <DRIVE_FOLDER_ID>/archive_YYYYMMDD_HHMMSS/
  4. Creates and uploads a manifest JSON to GCS after all processing
     and archival is complete.
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

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Required env vars ────────────────────────────────────────────────────────
PROJECT_ID      = os.environ["GCP_PROJECT_ID"]
BUCKET_NAME     = os.environ["POS_BUCKET_NAME"]
FOLDER_ID       = os.environ["DRIVE_FOLDER_ID"]
SERVICE_ACCOUNT = os.environ["SERVICE_ACCOUNT"]   # e.g. my-sa@my-project.iam.gserviceaccount.com

# ── Optional env vars ────────────────────────────────────────────────────────
ARCHIVE_FOLDER_NAME = os.environ.get("ARCHIVE_FOLDER_NAME", "archive")
INCOMING_PREFIX     = os.environ.get("INCOMING_PREFIX", "pos-raw-data/incoming-files")
MANIFESTS_PREFIX    = os.environ.get("MANIFESTS_PREFIX", "manifests")
RUN_LABEL           = os.environ.get("RUN_LABEL", "")

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

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/devstorage.read_write",
]


def get_credentials():
    """
    Use ADC to get credentials with Drive scope.
    On Cloud Run the attached service account is used automatically.
    """
    credentials, _ = google.auth.default(scopes=DRIVE_SCOPES)
    return credentials


# ── Drive helpers ─────────────────────────────────────────────────────────────

def build_drive_service(credentials):
    """Build Drive service using the already-resolved credentials."""
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def list_files(drive) -> list[dict]:
    """
    List all non-trashed files (not folders) in FOLDER_ID.
    """
    query = (
        f"'{FOLDER_ID}' in parents "
        f"and trashed = false "
        f"and mimeType != '{FOLDER_MIME}'"
    )
    results, page_token = [], None
    while True:
        resp = drive.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
            pageToken=page_token,
            pageSize=100,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
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
    folder = drive.files().create(
        body=metadata,
        fields="id, name",
        supportsAllDrives=True,
    ).execute()
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
        supportsAllDrives=True,
    ).execute()


# ── GCS helpers ───────────────────────────────────────────────────────────────

def gcs_blob_name(filename: str) -> str:
    return f"{INCOMING_PREFIX}/{filename}"


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
    submitted_by reflects the Cloud Run service account.
    """
    return {
        "run_id": run_id,
        "submitted_by": f"service-account:{service_account_email}",
        "submitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    credentials = get_credentials()
    log.info("Running as service account: %s", SERVICE_ACCOUNT)

    drive = build_drive_service(credentials)
    gcs   = storage.Client(project=PROJECT_ID, credentials=credentials)

    # ── STEP 1: List files to process ────────────────────────────────────────
    log.info("Scanning Drive folder %s", FOLDER_ID)
    files = list_files(drive)
    log.info("Found %d file(s) to process", len(files))

    if not files:
        log.info("No files to process. Exiting.")
        return

    # ── STEP 2: Sync — download from Drive and upload to GCS ─────────────────
    transferred = skipped = failed = 0
    archive_failed = 0               # initialized here so exit code check always works
    transferred_gcs_paths = []
    transferred_files = []           # track file objects that synced successfully

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
            transferred_files.append(file)   # only added on success
            transferred += 1

        except Exception as exc:
            log.error("FAILED: %s — %s", fname, exc)
            failed += 1

    log.info(
        "Sync done. transferred=%d  skipped=%d  failed=%d",
        transferred, skipped, failed,
    )

    # ── STEP 3: Archive — only archive files that synced successfully ─────────
    if not transferred_files:
        log.info("No files successfully transferred — skipping archive.")
    else:
        log.info(
            "Archiving %d successfully transferred file(s) into dated sub-folder …",
            len(transferred_files),
        )
        archive_folder_id = create_archive_folder(drive)

        archived = archive_failed = 0
        for file in transferred_files:    # ← only successfully synced files
            try:
                move_to_archive(drive, file["id"], archive_folder_id)
                log.info("Archived: %s", file["name"])
                archived += 1
            except HttpError as exc:
                log.error("ARCHIVE FAILED: %s — %s", file["name"], exc)
                archive_failed += 1

        log.info("Archive done. archived=%d  failed=%d", archived, archive_failed)

    # ── STEP 4: Manifest — only runs after sync + archive are fully complete ──
    log.info("Building and uploading manifest …")
    run_id   = build_run_id()
    manifest = build_manifest(run_id, transferred_gcs_paths, SERVICE_ACCOUNT)
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