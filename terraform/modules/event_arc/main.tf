resource "google_eventarc_trigger" "gcs_workflow_trigger" {
  name            = var.trigger_name
  location        = var.location
  project         = var.project_id
  service_account = var.service_account_email

  # Event type: Cloud Audit Log entry written
  matching_criteria {
    attribute = "type"
    value     = "google.cloud.audit.log.v1.written"
  }

  # Service producing the log: GCS
  matching_criteria {
    attribute = "serviceName"
    value     = "storage.googleapis.com"
  }

  # Operation: object created (covers all upload methods — gsutil, API, console)
  matching_criteria {
    attribute = "methodName"
    value     = "storage.objects.create"
  }

  # Folder + extension filter — only manifest JSONs in the manifests/ folder.
  # The "*.json" suffix prevents accidental triggers from non-manifest uploads.
  matching_criteria {
    attribute = "resourceName"
    operator  = "match-path-pattern"
    value     = "projects/_/buckets/${var.bucket_name}/objects/${var.folder_prefix}/*.json"
  }

  destination {
    workflow = "projects/${var.project_id}/locations/${var.region}/workflows/${var.workflow_name}"
  }

  # Order: audit logging must exist before the trigger references it
  depends_on = [google_project_iam_audit_config.gcs_audit]
}


resource "google_project_iam_audit_config" "gcs_audit" {
  project = var.project_id
  service = "storage.googleapis.com"

  audit_log_config {
    log_type = "ADMIN_READ"
  }
  audit_log_config {
    log_type = "DATA_READ"
  }
  audit_log_config {
    log_type = "DATA_WRITE"
  }
}

# ─────────────────────────────────────────────────────────────
# IAM bindings the trigger SA needs
# ─────────────────────────────────────────────────────────────
# GCS service agent needs to publish to Pub/Sub (Eventarc uses Pub/Sub
# under the hood for storage events)
data "google_storage_project_service_account" "gcs_sa" {
  project = var.project_id
}

resource "google_pubsub_topic_iam_member" "gcs_publisher_on_eventarc_topic" {
  project = var.project_id
  topic   = google_eventarc_trigger.gcs_workflow_trigger.transport[0].pubsub[0].topic
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_storage_project_service_account.gcs_sa.email_address}"
}
