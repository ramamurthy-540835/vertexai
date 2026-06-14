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
    time    = {}
  }
}

terraform {
  backend "gcs" {
    bucket = "gcp-gcs-cicd-core-mbr-bc-lead"
    prefix = "np/p-601-np-bcleadsmgmt-adt"
  }
}

moved {
  from = google_storage_bucket.this
  to   = module.main_bucket.google_storage_bucket.this
}

moved {
  from = google_storage_bucket.logging_bucket
  to   = module.logging_bucket.google_storage_bucket.this
}

moved {
  from = google_service_account.my_service_account
  to   = module.project_init.google_service_account.main
}

module "project_init" {
  source                       = "../../../modules/project_init"
  project_id                   = var.projectId
  service_account_id           = "gco-iam-svc-lead-mgmt-bc-adt"
  service_account_display_name = "gco-iam-svc-lead-mgmt-bc-adt"
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
  log_object_prefix = "logs/gcp-gcs-${var.prefix}-${var.country}-${var.environment}/"
}

module "pos_bucket" {
  source            = "../../../modules/gcs_bucket"
  project_id        = var.projectId
  bucket_name       = "gcp-gcs-${var.prefix}-${var.country}-${var.environment}-pos-raw"
  location          = var.location
  labels            = var.labels
  log_bucket        = module.logging_bucket.bucket_name
  log_object_prefix = "logs/gcp-gcs-${var.prefix}-${var.country}-${var.environment}-pos-raw/"
}

module "kubeflow_registry" {
  source        = "../../../modules/artifact_registry"
  location      = var.location
  repository_id = "gcp-lead-mgmt-kubeflow"
  description   = "Kubeflow pipeline repository"
  format        = "kfp"
  project       = var.projectId
}

module "service_now_username" {
  source        = "../../../modules/secret_manager"
  project       = var.projectId
  secret_id     = "lead_mgmt_snow_user"
  secret_value  = var.service_now_username
}

module "service_now_password" {
  source        = "../../../modules/secret_manager"
  project       = var.projectId
  secret_id     = "lead_mgmt_snow_password"
  secret_value  = var.service_now_password
}

module "service_now_client_id" {
  source        = "../../../modules/secret_manager"
  project       = var.projectId
  secret_id     = "service_now_client_id"
  secret_value  = var.service_now_client_id
}

module "service_now_client_secret" {
  source        = "../../../modules/secret_manager"
  project       = var.projectId
  secret_id     = "service_now_client_secret"
  secret_value  = var.service_now_client_secret
}

module "costco_tlsi_cert" {
  source        = "../../../modules/secret_manager"
  project       = var.projectId
  secret_id     = "costco_tlsi_cert"
  secret_value  = var.costco_tlsi_cert
}

module "cloud_sql_instance" {
  source              = "../../../modules/database"
  project             = var.projectId
  instance_name       = "lead-mgmt-adt"
  database_version    = "POSTGRES_15"
  region              = "us-central1"
  tier                = "db-custom-4-15360"
  edition             = "ENTERPRISE"
  availability_type   = "REGIONAL"
  activation_policy   = "ALWAYS"
  disk_size           = 100
  service_account     = var.gcp_workload_identity_sa_email
  service_account_iam = module.project_init.service_account_email
  host_project_id     = "gcp-prj-transit-hub"
  vpc_name            = "gcp-vpc-np-host"
  private_network     = "projects/gcp-prj-transit-hub/global/networks/gcp-vpc-np-host"
  subnetwork          = "gcp-snt-np-usc1-601-cloudruncloudsql-np"
  database_name       = "lead-mgmt-db"
  password            = var.db_password
}

module "monitoring_alert" {
  source = "../../../modules/monitoring_alert"

  display_name                 = "Lead_mgmt - Email Alert"
  email_address                = "c_upandey@costco.com"
  policy_display_name          = "Cloud Run Job Failure Alert"
  first_condition_display_name = "Match Job Failure Condition"
  second_condition_display_name = "SNOW Sync Job Failure Condition"
  force_recreate                = "3SS"
}

module "iam_management" {
  source     = "../../../modules/iam_management"
  project_id = var.projectId
  bucket_iam_bindings = [
    {
      bucket = "gcp-gcs-lead-mgmt-us-adt"
      role   = "roles/storage.legacyBucketOwner"
      member = "serviceAccount:${module.project_init.service_account_email}"
    }
  ]
}

module "workflows" {
  source                = "../../../modules/workflows"
  project_id            = var.projectId
  region                = var.region
  service_account_email = module.project_init.service_account_email
  workflow_name = "snow_sync_workflow"
  workflow_path = "../../../modules/workflows/snow_sync_workflow.yaml"
  workflow_description = "Orchestrates data sync from ServiceNow to GCP"
}

module "lead_match_workflow" {
  source                = "../../../modules/workflows"
  project_id            = var.projectId
  region                = var.region
  service_account_email = module.project_init.service_account_email
  workflow_name = "lead_match_workflow"
  workflow_path = "../../../modules/workflows/lead_match_workflow.yaml"
  workflow_description = "Exact matching between leads and sales data"
}

module "pos_ingestion_workflow" {
  source                = "../../../modules/workflows"
  project_id            = var.projectId
  region                = var.region
  service_account_email = module.project_init.service_account_email
  workflow_name = "pos_ingestion_workflow"
  workflow_path = "../../../modules/workflows/pos_dataflow_workflow.yaml"
  workflow_description = "Ingestion of point of sales data"
}


module "security_monitoring" {
  source                = "../../../modules/security_monitoring"
  project_id            = var.projectId
  notification_channels = [module.monitoring_alert.notification_channel_id]
}

###############################################################################
# 1. Pub/Sub layer — GCS folder → topic
###############################################################################
module "gcs_pubsub_trigger" {
  source = "../../../modules/gcs_pubsub_trigger"

  project_id  = var.projectId
  region      = var.region
  environment = var.environment

  bucket_name   = module.pos_bucket.bucket_name
  folder_prefix = "manifests/"

  topic_name = "gcs-file-events-${var.environment}"

  labels = {
    managed-by  = "terraform"
    environment = var.environment
    team        = "membership-gcp"
  }

  # message_retention_duration = "86400s"   # default ~24h
}

###############################################################################
# 2. Eventarc layer — topic → workflow
###############################################################################
module "gcs_eventarc_workflow_trigger" {
  source = "../../../modules/event_arc"

  project_id            = var.projectId
  location              = var.region
  trigger_name          = "pos-manifest-trigger"
  pubsub_topic_id       = module.gcs_pubsub_trigger.topic_id
  workflow_name         = "pos_ingestion_workflow"
  workflow_location     = var.region
  service_account_email = module.project_init.service_account_email
}

###############################################################################
# Outputs
###############################################################################
output "topic_id" {
  value       = module.gcs_pubsub_trigger.topic_id
  description = "Pub/Sub topic ID"
}

output "deadletter_topic_id" {
  value       = module.gcs_pubsub_trigger.deadletter_topic_id
  description = "Dead-letter topic ID"
}

output "eventarc_trigger_name" {
  value       = module.gcs_eventarc_workflow_trigger.eventarc_trigger_name
  description = "Eventarc trigger name"
}


module "network_attachement" {
  source                = "../../../modules/network_attachement" 
  project_id            = var.projectId
  region                = var.region
  vpc_project_id        = "gcp-prj-transit-hub"
  subnet_name           = "gcp-snt-np-usc1-601-bcleadsmgmt-servicenow-psc-adt"
  network_attachment_name = "gcp-nat-np-usc1-601-bcleadsmgmt-servicenow-psc-adt"
}

###############################################################################
# Cloud Armor Enterprise Enrollment
###############################################################################
resource "google_compute_project_cloud_armor_tier" "cloud_armor_tier" {
  project          = var.projectId
  cloud_armor_tier = "CA_ENTERPRISE_ANNUAL"
}
