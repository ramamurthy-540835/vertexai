variable "project_id" {
  type        = string
  description = "GCP project ID"
}

variable "location" {
  type        = string
  description = "Eventarc trigger location. Must be a single region (not multi-region) and match the workflow region."
}

variable "trigger_name" {
  type        = string
  description = "Name of the Eventarc trigger"
}

variable "pubsub_topic_id" {
  type        = string
  description = "Full resource ID of the Pub/Sub topic to subscribe to (projects/.../topics/...). Output from pubsub_gcs_trigger module."
}

variable "workflow_name" {
  type        = string
  description = "Name of the destination Cloud Workflow"
}

variable "workflow_location" {
  type        = string
  description = "Region of the destination Cloud Workflow"
}

variable "service_account_email" {
  type        = string
  description = "Service account Eventarc impersonates to invoke the workflow"
}