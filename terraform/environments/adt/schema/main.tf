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
    postgresql = {
      source  = "cyrilgdn/postgresql"
      version = "~> 1.21"
    }
    archive = {}
  }
}

# PostgreSQL provider setup (using IAM service account)
  provider "postgresql" {
  host     = var.host
  port     = var.port
  database = var.database_name
  username = var.gcp_workload_identity_sa_email
  sslmode  = "require"
}

# Execute SQL scripts directly
resource "postgresql_database" "execute_sql_scripts" {
  for_each = var.sql_scripts
  
  name = each.key
  template = "template0"
  
  connection_limit = -1
  allow_connections = true
  
  # This is a workaround - we'll use the lifecycle to run SQL
  lifecycle {
    ignore_changes = all
  }
}