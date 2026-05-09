variable "project_id" {
  type = string
}


variable "region" { # Could be named 'gcp_region' or 'region'
  type = string
}

variable "workflow_name" {
  description = "The name of the workflow."
  type        = string
}

variable "workflow_path" {
  description = "The file path of the workflow."
  type        = string
}

variable "workflow_description" {
  description = "A description for the workflow."
  type        = string
  default     = "Orchestrates data sync from ServiceNow to GCS and then to a database using Cloud Run jobs then run match job."
}

variable "workflow_region" {
  description = "The region where the workflow will be deployed."
  type        = string
  default     = "us-central1"
}

variable "service_account_email" {
  description = "Service Account email to run the workflow"
  type        = string
}
