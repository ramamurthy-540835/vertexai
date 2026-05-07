variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region (must match the workflow region)"
  type        = string
}

variable "bucket_name" {
  description = "Name of the existing GCS bucket to watch"
  type        = string
}

variable "folder_prefix" {
  description = <<-EOT
    GCS object prefix to scope the notification (acts as the folder filter).
    Must end with '/'. Examples: "uploads/", "data/raw/", "ingest/orders/"
  EOT
  type = string

  validation {
    condition     = endswith(var.folder_prefix, "/")
    error_message = "folder_prefix must end with '/' (e.g. 'uploads/')."
  }
}

variable "topic_name" {
  description = "Name for the Pub/Sub topic (subscription and dead-letter names are derived from this)"
  type        = string
}

variable "workflow_name" {
  description = "Name of the existing Cloud Workflow to trigger"
  type        = string
}

variable "service_account_email" {
  description = <<-EOT
    Email of the service account used to authenticate the Pub/Sub push call
    to the Workflows REST API (OIDC token on the push subscription).
    This SA must already exist and have roles/workflows.invoker granted at project level.
    Pub/Sub publisher role is NOT needed here — it is granted at project level externally.
  EOT
  type = string
}

variable "environment" {
  description = "Environment name (dev / staging / prod) — used in notification attributes and labels"
  type        = string
}

# ── Optional / tunable ────────────────────────────────────────────────────────

variable "labels" {
  description = "Labels applied to all Pub/Sub resources"
  type        = map(string)
  default     = {}
}

variable "message_retention_duration" {
  description = "How long Pub/Sub retains unacknowledged messages (seconds string)"
  type        = string
  default     = "86600s" # ~24 hours
}

variable "ack_deadline_seconds" {
  description = "Acknowledgement deadline for the push subscription"
  type        = number
  default     = 60
}

variable "max_delivery_attempts" {
  description = "Number of delivery attempts before a message is sent to the dead-letter topic"
  type        = number
  default     = 5
}

variable "retry_minimum_backoff" {
  description = "Minimum backoff duration between push retries"
  type        = string
  default     = "10s"
}

variable "retry_maximum_backoff" {
  description = "Maximum backoff duration between push retries"
  type        = string
  default     = "300s"
}
