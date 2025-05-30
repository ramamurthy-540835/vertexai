provider "google" {
  project = var.projectId
  region  = var.region
  user_project_override = true
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
    bucket = "gcp-gcs-cicd-core-mbr-bc-lead"
    #bucket = "gcp-gcs-cicd-core-gmp-membership"
    prefix = "adt/bc"
  }
}

resource "google_project_service" "required_apis" {
  for_each = toset([
    "pubsub.googleapis.com",
    "monitoring.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudfunctions.googleapis.com",
    "storage.googleapis.com",
    "cloudscheduler.googleapis.com"
  ])

  project = var.projectId
  service = each.key

  disable_on_destroy = false
}


resource "google_storage_bucket" "this" {
  name          = "cs-${var.prefix}-${var.country}-${var.environment}"
  location      = var.location
  force_destroy = true

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  project                     = var.projectId
  labels                      = var.labels
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

# module "docker_registry" {
 # source        = "../../modules/artifact_registry"
 # location      =  var.location
 # repository_id = "gcr.io"
 # description   = "Docker repository"
 # format        = "Docker"
# }

#module "kubeflow_registry" {
 # source        = "../../modules/artifact_registry"
  #location      =  var.location
  #repository_id = "lead_mgmt_kubeflow"
  #description   = "Kubeflow pipeline repository"
  #format        = "Kubeflow Pipelines"
#}

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

module "cloud_sql_instance" {
  source            = "../../modules/database"
  project           = "p-601-np-membership-adt"
  instance_name     = "lead_mgmt_adt"
  database_version  = "POSTGRES_15"
  region            = "us-central1"
  tier              = "db-custom-4-16384"
  edition           = "ENTERPRISE"
  availability_type = "REGIONAL"
  activation_policy = "ALWAYS"
  disk_size         = 100
  service_account =   var.gcp_workload_identity_sa_email
}

module "snow_sync_scheduler" {
  source           = "../../modules/snow_sync_scheduler"
  topic_name       = "snow-gcp-sync-trigger"
  scheduler_name   = "snow-sync-scheduler-job"
  schedule         = "0 2 * * *"                  # 2:00 AM UTC
  time_zone        = "UTC"
  attempt_deadline = "320s"
  data             = "Triggering SNOW to GCP sync job"
}

module "monitoring_alert" {
  source = "../../modules/monitoring_alert"

  display_name                 = "Lead_mgmt - Email Alert"
  email_address                = "membership_mit_team@costco.com"
  policy_display_name          = "Cloud Run Job Failure Alert"
  first_condition_display_name = "Match Job Failure Condition"
  second_condition_display_name = "SNOW Sync Job Failure Condition"
}