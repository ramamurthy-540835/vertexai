variable "project_id" {
  description = "GCP project ID where the Cloud Run service is deployed"
  type        = string
}

variable "region" {
  description = "Region for the Cloud Run service"
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Name of the Cloud Run service"
  type        = string
}

variable "image" {
  description = "Full image reference including tag/digest, e.g. us-docker.pkg.dev/.../cloud-run-service:<sha>"
  type        = string
}

variable "service_account_email" {
  description = "Service account email the Cloud Run service runs as"
  type        = string
}

variable "network" {
  description = "Full VPC network self-link for direct VPC egress"
  type        = string
}

variable "subnet" {
  description = "Full subnetwork self-link for direct VPC egress"
  type        = string
}

variable "env_vars" {
  description = "Map of environment variables for the container, sourced from the environment's .properties file"
  type        = map(string)
  default     = {}
}

variable "memory" {
  description = "Memory limit for the container"
  type        = string
  default     = "512Mi"
}

variable "cpu" {
  description = "CPU limit for the container"
  type        = string
  default     = "1"
}

variable "min_instances" {
  description = "Minimum number of container instances"
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Maximum number of container instances"
  type        = number
  default     = 2
}

variable "timeout_seconds" {
  description = "Request timeout in seconds"
  type        = number
  default     = 60
}

variable "enable_health_scheduler" {
  description = "Whether to create the Cloud Scheduler health-check job"
  type        = bool
  default     = true
}

variable "scheduler_cron" {
  description = "Cron schedule for the health-check scheduler job"
  type        = string
  default     = "0 0 * * *"
}