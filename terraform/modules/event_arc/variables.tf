variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "location" {
  description = "Eventarc trigger location — must match the bucket's region or multi-region"
  type        = string
}

variable "region" {
  description = "Region of the destination Cloud Workflow"
  type        = string
}

variable "trigger_name" {
  description = "Name of the Eventarc trigger"
  type        = string
}

variable "bucket_name" {
  description = "GCS bucket name (without gs:// prefix)"
  type        = string
}

variable "folder_prefix" {
  description = "Folder inside the bucket to watch for manifests. No leading or trailing slash."
  type        = string
  default     = "manifests"
}

variable "workflow_name" {
  description = "Name of the Cloud Workflow that processes the manifest"
  type        = string
}

variable "service_account_email" {
  description = "Service account email Eventarc uses to invoke the workflow"
  type        = string
}