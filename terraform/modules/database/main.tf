# data "google_compute_network" "shared_vpc" {
# name    = var.vpc_name
# project = var.host_project_id  # <-- specify the project where the VPC actually lives
# }

 data "google_compute_subnetwork" "my-subnetwork" {
  name    = var.subnetwork
  region  = var.region
  project = var.host_project_id
}

resource "google_sql_database_instance" "this" {
  name             = var.instance_name # Must use only lowercase, numbers, hyphens
  project          = var.project
  database_version = var.database_version # Should be a supported MySQL or PostgreSQL version for Enterprise Plus
  region           = var.region

  settings {
    tier    = var.tier

    # Must be "ENTERPRISE_PLUS" to use data_cache_config
    edition = var.edition

    # Only include this block for Enterprise Plus and supported engines
    #data_cache_config {
    # data_cache_enabled = true
    #}

    backup_configuration {
      enabled = true
      # Uncomment only if using MySQL
      # binary_log_enabled = true
    }

     ip_configuration {
      ipv4_enabled    = false
      private_network = data.google_compute_subnetwork.my-subnetwork.self_link
      ssl_mode = "ENCRYPTED_ONLY"
    }

    availability_type = var.availability_type
    activation_policy = var.activation_policy
    deletion_protection_enabled = true
    disk_autoresize = true
    disk_size = var.disk_size

    insights_config {
      query_insights_enabled = true
    }

    # Ensure this flag is compatible with your engine/version
    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
  }
}

resource "google_sql_user" "iam_service_account_user" {
  # Note: for Postgres only, GCP requires omitting the ".gserviceaccount.com" suffix
  # from the service account email due to length limits on database usernames.
  project  = var.project
  name     = trimsuffix(var.service_account, ".gserviceaccount.com")
  instance = google_sql_database_instance.this.name
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}
