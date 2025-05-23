provider "google" {
  project = var.projectId
}

terraform {
  required_providers {
    google = {
      source = "hashicorp/google"
      # Ensure version is at least 4.22.0 - released May 2022
      version = ">= 4.22.0"
    }
    archive = {}
  }
}

terraform {
  backend "gcs" {
    #bucket = "gcp-gcs-cicd-core-mbr-bc-lead"
    bucket = "gcp-gcs-cicd-core-gmp-membership"
    prefix = "adt/bc"
  }
}


resource "google_storage_bucket" "this" {
  name          = "cs-${var.prefix}-${var.country}-${var.environment}"
  location      = var.location
  force_destroy = true

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  project                     = var.projectId
  labels        = var.labels
  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 7
    }
    action {
      type = "Delete"
    }
  }
  lifecycle_rule {
    condition {
      with_state = "ARCHIVED"
      num_newer_versions = 2
    }
    action {
      type = "Delete"
    }
  }
}

module "docker_registry" {
  source        = "../../modules/artifact_registry"
  location      =  var.location
  repository_id = "gcr.io"
  description   = "Docker repository"
  format        = "Docker"
}

module "kubeflow_registry" {
  source        = "../../modules/artifact_registry"
  location      =  var.location
  repository_id = "lead_mgmt_kubeflow"
  description   = "Kubeflow pipeline repository"
  format        = "Kubeflow Pipelines"
}

module "service_now_username" {
 source        = "../../modules/secret_manager"
 project       = var.projectId
 secret_id     = "lead_mgmt_snow_user"
 secret_value  = "super-secret-value"
}

module "service_now_password" {
 source        = "../../modules/secret_manager"
 project       = var.projectId
 secret_id     = "lead_mgmt_snow_password"
 secret_value  = "super-secret-value"
}
