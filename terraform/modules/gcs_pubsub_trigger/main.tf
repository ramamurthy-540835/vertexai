  data "google_project" "project" {
  project_id = var.project_id
}
  # ── Pub/Sub Topic ─────────────────────────────────────────────────────────────

  resource "google_pubsub_topic" "this" {
    name    = var.topic_name
    project = var.project_id
    labels  = var.labels

    message_retention_duration = var.message_retention_duration

    message_storage_policy {
      allowed_persistence_regions = [var.region]
    }
  }

  # ── Dead-letter Topic ─────────────────────────────────────────────────────────

  resource "google_pubsub_topic" "deadletter" {
    name    = "${var.topic_name}-deadletter"
    project = var.project_id
    labels  = var.labels
  }

  data "google_storage_project_service_account" "gcs_sa" {
    project = var.project_id
  }

  resource "google_pubsub_topic_iam_member" "gcs_sa_publisher" {
    project = var.project_id
    topic   = google_pubsub_topic.this.name
    role    = "roles/pubsub.publisher"
    member  = "serviceAccount:${data.google_storage_project_service_account.gcs_sa.email_address}"
  }

  # ── GCS Bucket Notification (folder-scoped) ───────────────────────────────────

  resource "google_storage_notification" "this" {
    bucket         = var.bucket_name
    payload_format = "JSON_API_V1"
    topic          = google_pubsub_topic.this.id
    event_types    = ["OBJECT_FINALIZE"]

    object_name_prefix = var.folder_prefix

    custom_attributes = {
      env    = var.environment
      source = "gcs-pubsub-trigger"
    }

    depends_on = [google_pubsub_topic_iam_member.gcs_sa_publisher]
  }

  # ── Push Subscription → Cloud Workflow ────────────────────────────────────────

  resource "google_pubsub_subscription" "push" {
    name    = "${var.topic_name}-push-sub"
    project = var.project_id
    topic   = google_pubsub_topic.this.name
    labels  = var.labels

    ack_deadline_seconds       = var.ack_deadline_seconds
    message_retention_duration = var.message_retention_duration
    retain_acked_messages      = false

    push_config {
      push_endpoint = "https://workflowexecutions.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/workflows/${var.workflow_name}/executions"

      oidc_token {
        service_account_email = var.service_account_email
        audience              = "https://workflowexecutions.googleapis.com/"
      }

      attributes = {
        x-goog-version = "v1"
      }
    }

    dead_letter_policy {
      dead_letter_topic     = google_pubsub_topic.deadletter.id
      max_delivery_attempts = var.max_delivery_attempts
    }

    retry_policy {
      minimum_backoff = var.retry_minimum_backoff
      maximum_backoff = var.retry_maximum_backoff
    }
  }

  # ── Dead-letter Pull Subscription (for alerting / inspection) ─────────────────

  resource "google_pubsub_subscription" "deadletter_pull" {
    name    = "${var.topic_name}-deadletter-sub"
    project = var.project_id
    topic   = google_pubsub_topic.deadletter.name
    labels  = var.labels

    ack_deadline_seconds       = 60
    message_retention_duration = "604800s" # 7 days 
  }

# Pub/Sub service agent — publish to dead-letter topic
resource "google_pubsub_topic_iam_member" "pubsub_sa_deadletter_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.deadletter.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

# Pub/Sub service agent — subscribe on push sub to forward to dead-letter
resource "google_pubsub_subscription_iam_member" "pubsub_sa_push_subscriber" {
  project      = var.project_id
  subscription = google_pubsub_subscription.push.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_service_account_iam_member" "pubsub_token_creator" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${var.service_account_email}"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}