variable "instance_name" {
  description = "The name of the Cloud SQL instance"
  type        = string
}

variable "database_version" {
  description = "The database engine type and version"
  type        = string
}

variable "region" {
  description = "The region of the Cloud SQL instance"
  type        = string
}

variable "tier" {
  description = "The tier for the database instance"
  type        = string
}

variable "edition" {
  description = "The edition of the SQL instance (e.g., ENTERPRISE)"
  type        = string
  default     = "ENTERPRISE"
}

variable "availability_type" {
  description = "Specifies whether the instance should be zonal or regional"
  type        = string
  default     = "ZONAL"
}

variable "activation_policy" {
  description = "The activation policy for the instance (ALWAYS, NEVER, ON_DEMAND)"
  type        = string
  default     = "ALWAYS"
}

variable "disk_size" {
  description = "The size of data disk in GB"
  type        = number
  default     = 10
}
variable "service_account" {
  description = "service account for iam authentication"
  type        = string
  default     = ${{ secrets.GCP_WORKLOAD_IDENTITY_SA_EMAIL }}
}
