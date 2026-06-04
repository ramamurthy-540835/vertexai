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

Resume behaviour:
  - Files already in GCS are skipped for upload but still tracked
    for archival and manifest.
  - Files already archived in Drive are skipped (not re-moved).
  - If a manifest for this run already exists in GCS it is not re-uploaded.
  - A checkpoint file (gs://<bucket>/<INCOMING_PREFIX>/.run_checkpoint.json)
    tracks run_id and archive_folder_id across retries so the same
    run_id and archive folder are reused on resume.
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
SERVICE_ACCOUNT = os.environ["SERVICE_ACCOUNT"]

# ── Optional env vars ────────────────────────────────────────────────────────
ARCHIVE_FOLDER_NAME = os.environ.get("ARCHIVE_FOLDER_NAME", "archive")
INCOMING_PREFIX     = os.environ.get("INCOMING_PREFIX", "pos-raw-data/incoming-files")
MANIFESTS_PREFIX    = os.environ.get("MANIFESTS_PREFIX", "manifests")
RUN_LABEL           = os.environ.get("RUN_LABEL", "")

# ── Checkpoint blob — tracks run_id + archive_folder_id across retries ───────
CHECKPOINT_BLOB = f"{INCOMING_PREFIX}/.run_checkpoint.json"

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

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/devstorage.read_write",
]


# ── Auth helpers ──────────────────────────────────────────────────────────────

def get_credentials():
    """
    Use ADC to get credentials with Drive scope.
    On Cloud Run the attached service account is used automatically.
    """
    credentials, _ = google.auth.default(scopes=DRIVE_SCOPES)
    return credentials


# ── Checkpoint helpers ────────────────────────────────────────────────────────

def load_checkpoint(bucket) -> dict:
    """
    Load checkpoint from GCS if it exists.
    Returns dict with run_id and archive_folder_id, or empty dict.
    """
    blob = bucket.blob(CHECKPOINT_BLOB)
    if blob.exists():
        data = json.loads(blob.download_as_text())
        log.info(
            "Resuming from checkpoint: run_id=%s  archive_folder_id=%s",
            data.get("run_id"), data.get("archive_folder_id"),
        )
        return data
    return {}


def save_checkpoint(bucket, run_id: str, archive_folder_id: str) -> None:
    """Save checkpoint to GCS so retries can resume from the same run."""
    data = {
        "run_id": run_id,
        "archive_folder_id": archive_folder_id,
        "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    bucket.blob(CHECKPOINT_BLOB).upload_from_string(
        json.dumps(data, indent=2).encode("utf-8"),
        content_type="application/json",
    )
    log.info(
        "Checkpoint saved: run_id=%s  archive_folder_id=%s",
        run_id, archive_folder_id,
    )


def clear_checkpoint(bucket) -> None:
    """Delete checkpoint after a fully successful run."""
    blob = bucket.blob(CHECKPOINT_BLOB)
    if blob.exists():
        blob.delete()
        log.info("Checkpoint cleared")


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


def is_already_archived(drive, file_id: str, archive_folder_id: str) -> bool:
    """
    Check if the file is already in the archive folder.
    Prevents double-move on retry.
    """
    try:
        file_meta = drive.files().get(
            fileId=file_id,
            fields="parents",
            supportsAllDrives=True,
        ).execute()
        return archive_folder_id in file_meta.get("parents", [])
    except HttpError:
        return False


def get_or_create_archive_folder(drive, checkpoint: dict) -> str:
    """
    Reuse archive folder from checkpoint if available,
    otherwise create a new one.
    """
    if checkpoint.get("archive_folder_id"):
        folder_id = checkpoint["archive_folder_id"]
        log.info("Reusing archive folder from checkpoint: %s", folder_id)
        return folder_id

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


def already_in_gcs(bucket, filename: str) -> bool:
    return bucket.blob(gcs_blob_name(filename)).exists()


def manifest_already_exists(bucket, run_id: str) -> bool:
    return bucket.blob(f"{MANIFESTS_PREFIX}/{run_id}.json").exists()


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


def upload_to_gcs(bucket, data: bytes, filename: str) -> str:
    blob_name = gcs_blob_name(filename)
    bucket.blob(blob_name).upload_from_string(data)
    return f"gs://{BUCKET_NAME}/{blob_name}"


# ── Manifest helpers ──────────────────────────────────────────────────────────

def build_run_id(checkpoint: dict) -> str:
    """
    Reuse run_id from checkpoint if available, otherwise generate new one.
    """
    if checkpoint.get("run_id"):
        log.info("Reusing run_id from checkpoint: %s", checkpoint["run_id"])
        return checkpoint["run_id"]
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


def upload_manifest(bucket, manifest: dict, run_id: str) -> str:
    """
    Upload the manifest JSON to GCS under MANIFESTS_PREFIX/<run_id>.json.
    Returns the gs:// URI of the uploaded manifest.
    """
    blob_name = f"{MANIFESTS_PREFIX}/{run_id}.json"
    data = json.dumps(manifest, indent=2).encode("utf-8")
    bucket.blob(blob_name).upload_from_string(
        data, content_type="application/json"
    )
    uri = f"gs://{BUCKET_NAME}/{blob_name}"
    log.info("Manifest uploaded: %s", uri)
    return uri


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    credentials = get_credentials()
    log.info("Running as service account: %s", SERVICE_ACCOUNT)

    drive  = build_drive_service(credentials)
    gcs    = storage.Client(project=PROJECT_ID, credentials=credentials)
    bucket = gcs.bucket(BUCKET_NAME)

    # ── Load checkpoint (resume support) ─────────────────────────────────────
    checkpoint = load_checkpoint(bucket)
    run_id     = build_run_id(checkpoint)

    # ── STEP 1: List files to process ────────────────────────────────────────
    log.info("Scanning Drive folder %s", FOLDER_ID)
    files = list_files(drive)
    log.info("Found %d file(s) in Drive folder", len(files))

    if not files:
        log.info("No files to process. Exiting.")
        return

    # ── STEP 2: Sync — download from Drive and upload to GCS ─────────────────
    transferred = skipped = failed = 0
    transferred_gcs_paths = []
    transferred_files     = []

    for file in files:
        fname = file["name"]
        try:
            final_name = resolve_final_name(file)

            if already_in_gcs(bucket, final_name):
                log.info("SKIP upload (already in GCS): %s", final_name)
                # Still track — file is in GCS and needs archiving + manifest
                transferred_gcs_paths.append(
                    f"gs://{BUCKET_NAME}/{gcs_blob_name(final_name)}"
                )
                transferred_files.append(file)
                skipped += 1
                continue

            log.info("Downloading: %s (%s)", fname, file["mimeType"])
            data = download_file(drive, file)
            gcs_path = upload_to_gcs(bucket, data, final_name)
            log.info("Transferred: %s → %s", fname, gcs_path)
            transferred_gcs_paths.append(gcs_path)
            transferred_files.append(file)
            transferred += 1

        except Exception as exc:
            log.error("FAILED: %s — %s", fname, exc)
            failed += 1

    log.info(
        "Sync done. transferred=%d  skipped=%d  failed=%d",
        transferred, skipped, failed,
    )

    # ── STEP 3: Archive — only archive files that synced successfully ─────────
    archive_failed = 0
    if not transferred_files:
        log.info("No files to archive — skipping.")
    else:
        log.info(
            "Archiving %d file(s) into dated sub-folder …",
            len(transferred_files),
        )
        archive_folder_id = get_or_create_archive_folder(drive, checkpoint)

        # Save checkpoint so retry reuses same run_id + archive folder
        save_checkpoint(bucket, run_id, archive_folder_id)

        archived = archive_failed = 0
        for file in transferred_files:
            try:
                if is_already_archived(drive, file["id"], archive_folder_id):
                    log.info("SKIP archive (already archived): %s", file["name"])
                    archived += 1
                    continue
                move_to_archive(drive, file["id"], archive_folder_id)
                log.info("Archived: %s", file["name"])
                archived += 1
            except HttpError as exc:
                log.error("ARCHIVE FAILED: %s — %s", file["name"], exc)
                archive_failed += 1

        log.info("Archive done. archived=%d  failed=%d", archived, archive_failed)

    # ── STEP 4: Manifest — only runs after archive fully completes ────────────
    if archive_failed:
        log.error(
            "Archive had %d failure(s) — skipping manifest to avoid incomplete run",
            archive_failed,
        )
        raise SystemExit(1)

    if manifest_already_exists(bucket, run_id):
        log.info(
            "Manifest already exists for run_id=%s — skipping upload",
            run_id,
        )
    else:
        log.info("Building and uploading manifest …")
        manifest = build_manifest(run_id, transferred_gcs_paths, SERVICE_ACCOUNT)
        log.info("Manifest contents:\n%s", json.dumps(manifest, indent=2))
        try:
            manifest_uri = upload_manifest(bucket, manifest, run_id)
            log.info(
                "Manifest creation complete. run_id=%s  files=%d  uri=%s",
                run_id, len(transferred_gcs_paths), manifest_uri,
            )
        except Exception as exc:
            log.error("MANIFEST UPLOAD FAILED: %s", exc)
            raise SystemExit(1)

    # ── Clear checkpoint on full success ──────────────────────────────────────
    clear_checkpoint(bucket)

    # ── Final exit code ───────────────────────────────────────────────────────
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    run()