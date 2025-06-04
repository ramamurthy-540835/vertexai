variable "projectId" { 
  description = "The ID of the GCP Project"
  type = string 
  default     = "p-601-np-membership-spt"  # Default for spt project
}

variable "environment" { 
  description = "GCP environment"
  type = string 
  default     = "spt"  # Default for spt project
}

variable "location" {
  description = "The geographic location for the dataset"
  type        = string
  default     = "US"
}

variable "labels" {
  description = "Resource labels"
  type        = map(string)
  default = {
    env = "spt"
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


