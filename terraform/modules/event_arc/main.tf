# ─────────────────────────────────────────────────────────────
# Eventarc Trigger
# ─────────────────────────────────────────────────────────────

resource "google_eventarc_trigger" "gcs_workflow_trigger" {
  name     = var.trigger_name
  location = var.location
  project  = var.project_id

  service_account = var.service_account_email

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }

  matching_criteria {
    attribute = "bucket"
    value     = var.bucket_name
  }

  destination {
    workflow = "projects/${var.project_id}/locations/${var.region}/workflows/${var.workflow_name}"
  }
}