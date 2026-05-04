variable "trigger_name" {
  description = "Name of the Eventarc trigger"
  type        = string
}

variable "region" {
  description = "Region where the Eventarc trigger will be created"
  type        = string
}

variable "bucket_name" {
  description = "Name of the GCS bucket to monitor"
  type        = string
}

variable "path" {
  description = "Folder path (prefix) inside the bucket to filter events"
  type        = string
}

variable "workflow_name" {
  description = "Full resource ID of the Workflows workflow"
  type        = string
}

variable "service_account_email" {
  description = "Service account email used by Eventarc trigger"
  type        = string
}

variable "project_id" {
  description = "GCP Project ID"
  type        = string
}