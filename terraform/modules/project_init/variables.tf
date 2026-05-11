variable "project_id" {
  description = "The ID of the GCP Project"
  type        = string
}

variable "services" {
  description = "List of GCP services to enable"
  type        = list(string)
  default     = [
    "pubsub.googleapis.com",
    "monitoring.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudfunctions.googleapis.com",
    "storage.googleapis.com",
    "cloudscheduler.googleapis.com",
    "servicenetworking.googleapis.com",
    "run.googleapis.com",
    "containerthreatdetection.googleapis.com",
    "logging.googleapis.com",
    "aiplatform.googleapis.com",
    "eventarc.googleapis.com",
    "dataflow.googleapis.com"
  ]
}

variable "service_account_id" {
  description = "The ID of the main service account"
  type        = string
}

variable "service_account_display_name" {
  description = "The display name of the main service account"
  type        = string
}
