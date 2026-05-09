variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "Region for the topic's message storage policy"
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
  description = "Name for the Pub/Sub topic (dead-letter topic and subscription names are derived from this)"
  type        = string
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
  description = "How long Pub/Sub retains unacknowledged messages on the main topic (seconds string)"
  type        = string
  default     = "86400s" # 24 hours
}