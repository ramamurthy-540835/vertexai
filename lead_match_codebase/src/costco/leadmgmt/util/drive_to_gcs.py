"""
Drive → GCS sync job.
Triggered by Cloud Scheduler via Cloud Run Job.
Scans a Google Drive folder, downloads each file, and uploads to GCS —
skipping files already present in the destination bucket.
"""

import io
import os
import logging

from dotenv import load_dotenv
from google.cloud import storage
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.auth import default as google_auth_default

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PROJECT_ID  = os.environ["GCP_PROJECT_ID"]
BUCKET_NAME = os.environ["INPUT_BUCKET_NAME"]
FOLDER_ID   = os.environ["DRIVE_FOLDER_ID"]
DEST_PREFIX = os.environ.get("GCS_DESTINATION_PREFIX", "").rstrip("/")

# Google Workspace files cannot be downloaded directly — export to Office formats
EXPORTABLE = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
}


def build_drive_service():
    creds, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_files(drive) -> list[dict]:
    """List all non-trashed files in the configured Drive folder."""
    query = f"'{FOLDER_ID}' in parents and trashed = false"
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


def gcs_blob_name(filename: str) -> str:
    """Compute the destination blob name (with optional prefix)."""
    return f"{DEST_PREFIX}/{filename}" if DEST_PREFIX else filename


def already_in_gcs(gcs: storage.Client, filename: str) -> bool:
    """Check whether a file already exists at the destination path."""
    bucket = gcs.bucket(BUCKET_NAME)
    return bucket.blob(gcs_blob_name(filename)).exists()


def resolve_final_name(file: dict) -> str:
    """
    Determine the final filename used for the GCS upload.
    Google Workspace files get an extension appended (.docx / .xlsx / .pptx).
    """
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
    bucket = gcs.bucket(BUCKET_NAME)
    bucket.blob(blob_name).upload_from_string(data)
    return f"gs://{BUCKET_NAME}/{blob_name}"


def run():
    drive = build_drive_service()
    gcs   = storage.Client(project=PROJECT_ID)

    log.info("Scanning Drive folder %s", FOLDER_ID)

    files = list_files(drive)
    log.info("Found %d file(s) in folder", len(files))

    transferred = skipped = failed = 0
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
            transferred += 1
        except Exception as exc:
            log.error("FAILED: %s — %s", fname, exc)
            failed += 1

    log.info("Done. transferred=%d skipped=%d failed=%d", transferred, skipped, failed)
    if failed:
        raise SystemExit(1)