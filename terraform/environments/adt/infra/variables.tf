variable "projectId" { 
  description = "The ID of the GCP Project"
  type = string 
  default     = "p-601-np-bcleadsmgmt-adt"  # Default for ADT project
}

variable "environment" { 
  description = "GCP environment"
  type = string 
  default     = "adt"  # Default for ADT project
}

variable "location" {
  description = "The geographic location for the dataset"
  type        = string
  default     = "us"
}

variable "labels" {
  description = "Resource labels"
  type        = map(string)
  default = {
    env = "adt"
  }
}

variable "region" {
  description = "Region for resources"
  type        = string
  default     = "us-central1"
}

variable "gcp_workload_identity_sa_email"{
description = "iam service account for cloud sql"
type = string
default = "gco-iam-svc-cicd-mbr-bc-np@gcp-prj-cicd-core.iam.gserviceaccount.com"
}

variable "prefix" {
  description = "prefix for naming convention"
  type        = string
  default     = "lead-mgmt"
}

variable "country" {
  description = "country information for a project"
  type        = string
  default     = "us"
}

variable "service_now_client_secret" {
  description = "ServiceNow client secret"
  type        = string
  sensitive   = true
}

variable "service_now_client_id" {
  description = "ServiceNow client id"
  type        = string
  sensitive   = true
}

variable "service_now_username" {
  description = "ServiceNow username"
  type        = string
  sensitive   = true
}

variable "service_now_password" {
  description = "ServiceNow password"
  type        = string
  sensitive   = true
}

variable "db_password"{
  description = "database password"
  type        = string
  sensitive   = true
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


variable "costco_tlsi_cert" {
  description = "Costco TLSi CA certificate chain (base64 encoded) for GCP egress TLS inspection"
  type        = string
  sensitive   = true
}

variable "service_image_tag" {
  description = "image tag for cloud run service"
  type        = string
  sensitive   = true
}