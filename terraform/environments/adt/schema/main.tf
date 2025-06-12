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

resource "null_resource" "execute_sql_scripts" {
 triggers = {
   sql_script_hash = filesha256("../../../../postgres_resources/lead_mgmt_schema_creation.sql")
 }
 provisioner "local-exec" {
   command = <<-EOT
     # Get access token for IAM authentication
     export PGPASSWORD=$(gcloud auth print-access-token)
     # Connect using private IP with IAM authentication
     psql "host=${var.host}\
           port=5432 \
           dbname=${var.database_name} \
           user=${var.gcp_workload_identity_sa_email} \
           sslmode=require" \
           -f "../../../../postgres_resources/lead_mgmt_schema_creation.sql"
   EOT
   environment = {
     CLOUDSQL_INSTANCE = "${var.projectId}:${var.region}:lead_mgmt_adt"
   }
 }

}

# Execute SQL scripts directly
#resource "postgresql_database" "execute_sql_scripts" {
#  for_each = var.sql_scripts
  
#  name = each.key
#  template = "template0"
  
#  connection_limit = -1
#  allow_connections = true
  
  # This is a workaround - we'll use the lifecycle to run SQL
#  lifecycle {
#    ignore_changes = all
#  }
# }