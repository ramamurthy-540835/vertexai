# ─────────────────────────────────────────────────────────────
# Data Sources
# ─────────────────────────────────────────────────────────────

data "google_project" "project" {
  project_id = var.project_id
}

data "google_storage_project_service_account" "gcs_sa" {
  project = var.project_id
}

# Allow Eventarc SA to receive Eventarc events
resource "google_project_iam_member" "event_receiver" {
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${var.service_account_email}"
}

# Allow GCS to publish events to Pub/Sub
resource "google_project_iam_member" "gcs_pubsub_publisher" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_storage_project_service_account.gcs_sa.email_address}"
}

# ─────────────────────────────────────────────────────────────
# Eventarc Trigger
# ─────────────────────────────────────────────────────────────

resource "google_eventarc_trigger" "gcs_workflow_trigger" {
  name     = var.trigger_name
  location = var.region
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

  transport {
    pubsub {
      topic = google_pubsub_topic.eventarc_transport.id
    }
  }
}

# ─────────────────────────────────────────────────────────────
# Transport Pub/Sub Topic (required by Eventarc)
# ─────────────────────────────────────────────────────────────

resource "google_pubsub_topic" "eventarc_transport" {
  name    = "${var.trigger_name}-transport"
  project = var.project_id
}