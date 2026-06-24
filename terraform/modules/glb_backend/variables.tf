variable "project_id" {
  description = "GCP project ID where the backend service is created"
  type        = string
}

variable "region" {
  description = "Region of the Cloud Run service and the regional NEG"
  type        = string
  default     = "us-central1"
}

variable "cloud_run_service_name" {
  description = "Name of the Cloud Run service to attach as a serverless NEG backend"
  type        = string
}

variable "backend_name" {
  description = "Name for the backend service. Defaults to <cloud_run_service_name>-backend if not set"
  type        = string
  default     = ""
}

variable "load_balancing_scheme" {
  description = "Load balancing scheme for the backend service (EXTERNAL_MANAGED or INTERNAL_MANAGED)"
  type        = string
  default     = "EXTERNAL_MANAGED"
}

variable "enable_logging" {
  description = "Whether to enable backend service request logging"
  type        = bool
  default     = true
}

variable "log_sample_rate" {
  description = "Sample rate for backend logging (0.0 to 1.0)"
  type        = number
  default     = 1.0
}