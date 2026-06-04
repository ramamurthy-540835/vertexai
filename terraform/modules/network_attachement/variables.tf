variable "project_id" {
  description = "The Vertex AI project ID where the network attachment is created."
  type        = string
}

variable "vpc_project_id" {
  description = "The VPC and subnet project_id"
  type        = string
}


variable "region" {
  description = "The GCP region for the network attachment and subnet."
  type        = string
}

variable "subnet_name" {
  description = "The name of the existing subnet."
  type        = string
}

variable "network_attachment_name" {
  description = "The name for the PSC network attachment."
  type        = string
}
