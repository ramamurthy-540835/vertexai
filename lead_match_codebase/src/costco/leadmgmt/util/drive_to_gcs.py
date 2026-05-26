"""
Drive → GCS sync job.
Triggered by GitHub Actions via Apps Script workflow_dispatch.

Apps Script acts as the gate — it only dispatches this job when
'process_pos_data.txt' is present in the Drive folder, and deletes
it afterward. This script therefore always runs unconditionally and:

  1. Lists all *files* (not sub-folders) in the Drive folder,
     excluding the trigger file.
  2. Downloads & uploads every file to GCS.
  3. Archives all processed files into a date-stamped sub-folder
     created inside the same Drive folder:
     <DRIVE_FOLDER_ID>/archive_YYYYMMDD_HHMMSS/
"""

import io
import os
import logging
from datetime import datetime, timezone

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
TRIGGER_FILENAME    = os.environ.get("TRIGGER_FILENAME", "process_pos_data.txt")
ARCHIVE_FOLDER_NAME = os.environ.get("ARCHIVE_FOLDER_NAME", "archive")

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


# ── Drive helpers ─────────────────────────────────────────────────────────────

def build_drive_service():
    creds, _ = google_auth_default(
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


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
        "parents": [FOLDER_ID],              # created inside the same Drive folder
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
    return f"{DEST_PREFIX}/{filename}" if DEST_PREFIX else filename


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


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    drive = build_drive_service()
    gcs   = storage.Client(project=PROJECT_ID)

    log.info("Scanning Drive folder %s (trigger file excluded)", FOLDER_ID)

    files = list_files(drive)
    log.info("Found %d file(s) to process (trigger file and folders excluded)", len(files))

    if not files:
        log.info("No files to process. Exiting.")
        return

    # ── Sync: download and upload every file to GCS ───────────────────────────
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

    log.info(
        "Sync done. transferred=%d  skipped=%d  failed=%d",
        transferred, skipped, failed,
    )

    # ── Archive: move all processed files into dated sub-folder ──────────────
    # Sub-folder is created inside the same FOLDER_ID.
    # Trigger file is already excluded from the files list above.
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

    if failed or archive_failed:
        raise SystemExit(1)


if __name__ == "__main__":
    run()