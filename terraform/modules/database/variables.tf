variable "instance_name" {
  description = "The name of the Cloud SQL instance"
  type        = string
}

variable "project" {
  description = "Project id information"
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
}
variable "vpc_name" {
  description = "Name of the shared VPC"
  type        = string
}

variable "host_project_id" {
  description = "ID of the host project where the shared VPC is located"
  type        = string
}

variable "private_network" {
  description = "private network for the database"
  type        = string
}

variable "subnetwork" {
  description = "subnetwork for the database"
  type        = string
}

variable "database_name" {
  description = "Primary database name"
  type        = string
}

variable "password" {
  description = "Password for the super user"
  type        = string
}
