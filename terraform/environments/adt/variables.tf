variable "projectId" { 
  description = "The ID of the GCP Project"
  type = string 
  default     = "p-601-np-membership-adt"  # Default for ADT project
}

variable "environment" { 
  description = "GCP environment"
  type = string 
  default     = "adt"  # Default for ADT project
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
    env = "adt"
  }
}

variable "region" {
  description = "Region for resources"
  type        = string
  default     = "us-central1"
}
