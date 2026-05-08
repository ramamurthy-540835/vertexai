# ─────────────────────────────────────────────────────────────
# Data Sources
# ─────────────────────────────────────────────────────────────

data "google_project" "project" {
  project_id = var.project_id
}

data "google_storage_project_service_account" "gcs_sa" {
  project = var.project_id
}

# ─────────────────────────────────────────────────────────────
# IAM Permissions
# ─────────────────────────────────────────────────────────────

# Allow GCS service account to publish events
resource "google_pubsub_topic_iam_member" "gcs_pubsub_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.eventarc_transport.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${data.google_storage_project_service_account.gcs_sa.email_address}"
}

# Allow Eventarc service agent to publish to transport topic
resource "google_pubsub_topic_iam_member" "eventarc_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.eventarc_transport.name
  role    = "roles/pubsub.publisher"

  member = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-eventarc.iam.gserviceaccount.com"
}

# ─────────────────────────────────────────────────────────────
# Transport Pub/Sub Topic
# ─────────────────────────────────────────────────────────────

resource "google_pubsub_topic" "eventarc_transport" {
  name    = "${var.trigger_name}-transport"
  project = var.project_id
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

  depends_on = [
    google_project_iam_member.event_receiver,
    google_project_iam_member.gcs_pubsub_publisher,
    google_pubsub_topic_iam_member.eventarc_publisher
  ]
}