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

# ── GCS Bucket Notification (folder-scoped) ───────────────────────────────────
# NOTE: previously depended on google_pubsub_topic_iam_member.gcs_sa_publisher,
# which has been removed from this module (the roles/pubsub.publisher grant is
# now raised separately, from another project/state — see the consolidated
# pubsub_iam_bindings.tf). That IAM grant must exist before this notification
# works in practice, but Terraform can no longer express that dependency across
# states, so apply order must be handled operationally: IAM bindings first,
# then this module.
resource "google_storage_notification" "this" {
  bucket             = var.bucket_name
  payload_format     = "JSON_API_V1"
  topic              = google_pubsub_topic.this.id
  event_types        = ["OBJECT_FINALIZE"]
  object_name_prefix = var.folder_prefix
  custom_attributes = {
    env    = var.environment
    source = "gcs-pubsub-trigger"
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
