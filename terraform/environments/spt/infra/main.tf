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
    prefix = "np/p-601-np-bcleadsmgmt-spt"
  }
}

moved {
  from = google_storage_bucket.this
  to   = module.main_bucket.google_storage_bucket.this
}

moved {
  from = google_service_account.my_service_account
  to   = module.project_init.google_service_account.main
}

module "project_init" {
  source                       = "../../../modules/project_init"
  project_id                   = var.projectId
  service_account_id           = "gco-iam-svc-lead-mgmt-bc-spt"
  service_account_display_name = "gco-iam-svc-lead-mgmt-bc-spt"
}

module "logging_bucket" {
  source                    = "../../../modules/gcs_bucket"
  project_id                = var.projectId
  bucket_name               = "gcp-gcs-${var.prefix}-${var.country}-${var.environment}-logs"
  location                  = var.location
  labels                    = var.labels
  log_bucket                = "gcp-gcs-${var.prefix}-${var.country}-${var.environment}-logs"
  log_object_prefix         = "self-logs/"
  grant_logging_permissions = true
}

module "main_bucket" {
  source            = "../../../modules/gcs_bucket"
  project_id        = var.projectId
  bucket_name       = "gcp-gcs-${var.prefix}-${var.country}-${var.environment}"
  location          = var.location
  labels            = var.labels
  log_bucket        = module.logging_bucket.bucket_name
  log_object_prefix = "logs/"
}

resource "google_project_service" "required_apis_recreate" {
  for_each = toset([
    "pubsub.googleapis.com",
    "monitoring.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudfunctions.googleapis.com",
    "storage.googleapis.com",
    "cloudscheduler.googleapis.com",
    "servicenetworking.googleapis.com",
    "run.googleapis.com",
    "containerthreatdetection.googleapis.com"
  ]) 

  project = var.projectId
  service = each.value

  disable_on_destroy = false
}


/*module "kubeflow_registry" {
 source        = "../../../modules/artifact_registry"
 location      =  var.location
 repository_id = "gcp-lead-mgmt-kubeflow"
 description   = "Kubeflow pipeline repository"
 format        = "kfp"
 project       = var.projectId
}*/


module "service_now_username" {
 source        = "../../../modules/secret_manager"
 project       = var.projectId
 secret_id     = "lead_mgmt_snow_user"
 secret_value  = "lead.api.access"
}

module "service_now_password" {
 source        = "../../../modules/secret_manager"
 project       = var.projectId
 secret_id     = "lead_mgmt_snow_password"
 secret_value  = "Costco@web123"
}


module "cloud_sql_instance" {
  source            = "../../../modules/database"
  #depends_on        = [google_project_service.required_apis_recreate]
  project           = "p-601-np-bcleadsmgmt-spt"
  instance_name     = "lead-mgmt-spt"
  database_version  = "POSTGRES_15"
  region            = "us-central1"
  tier              = "db-custom-4-15360"
  edition           = "ENTERPRISE"
  availability_type = "REGIONAL"
  activation_policy = "ALWAYS"
  disk_size         = 100
  service_account   = var.gcp_workload_identity_sa_email
  service_account_iam = module.project_init.service_account_email
  host_project_id = "gcp-prj-transit-hub"
  vpc_name = "gcp-vpc-np-host"
  private_network = "projects/gcp-prj-transit-hub/global/networks/gcp-vpc-np-host"
  subnetwork = "gcp-snt-np-usc1-601-cloudruncloudsql-np"
  database_name = "lead-mgmt-db"
  password = "yI|m6?535*FZ"
  }
/*
module "snow_sync_scheduler" {
  source           = "../../../modules/snow_sync_scheduler"
  topic_name       = "snow-gcp-sync-trigger"
  scheduler_name   = "snow-sync-scheduler-job"
  schedule         = "0 2 * * *"                  # 2:00 AM UTC
  time_zone        = "UTC"
  attempt_deadline = "320s"
  data             = "Triggering SNOW to GCP sync job"
}
*/
module "monitoring_alert" {
  source = "../../../modules/monitoring_alert"

  display_name                 = "Lead_mgmt - Email Alert"
  email_address                = "membership_mit_team@costco.com"
  policy_display_name          = "Cloud Run Job Failure Alert"
  first_condition_display_name = "Match Job Failure Condition"
  second_condition_display_name = "SNOW Sync Job Failure Condition"
}

resource "google_storage_bucket_iam_member" "bucket_legacy_owner" {
  bucket = "gcp-gcs-lead-mgmt-us-spt"
  role   = "roles/storage.legacyBucketOwner"
  member = "serviceAccount:${module.project_init.service_account_email}"
}

