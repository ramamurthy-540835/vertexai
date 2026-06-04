variable "projectId" {
  description = "GCP project ID"
  type        = string
  default     = "p-601-pd-bcleadsmgmt-prd"
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "instance" {
  description = "Cloud SQL instance name"
  type        = string
  default     = "lead-mgmt-prd"
}

variable "host" {
  description = "PostgreSQL host (e.g., Cloud SQL IP address or hostname)"
  type        = string
  default     = ""
}

variable "port" {
  description = "PostgreSQL port"
  type        = number
  default     = 5432
}

variable "database_name" {
  description = "PostgreSQL database name"
  type        = string
  default     = "lead-mgmt-db"
}

variable "db_password"{
  description = "database password"
  type        = string
  sensitive   = true
}

variable "gcp_workload_identity_sa_email" {
  description = "IAM service account email used for Workload Identity authentication"
  type        = string
  default = "gco-iam-svc-cicd-mbr-bc-pd@gcp-prj-cicd-core.iam.gserviceaccount.com"
}

# SQL Scripts Configuration
variable "sql_scripts" {
  description = "Map of SQL script files to execute"
  type = map(object({
    file_path  = string
    always_run = bool  # Set to true to run script on every apply
  }))
  default = {}
}

variable "schema_name" {
  type = string
  description = "schema name"
  default = "lead_mgmt_prd"
}

variable iam_user{
  type = string
  description = "iam user"
  default = "gco-iam-svc-cicd-mbr-bc-pd@gcp-prj-cicd-core.iam"
}