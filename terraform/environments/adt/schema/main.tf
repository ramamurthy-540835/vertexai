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
  username = "gco-iam-svc-cicd-mbr-bc-np@gcp-prj-cicd-core.iam"
  sslmode  = "disable"
}

 resource "null_resource" "initial_database_setup" {
 triggers = {
   sql_script_hash = filesha256("../../../../postgres_resources/lead_mgmt_schema_creation.sql")
 }
 provisioner "local-exec" {
   command = <<-EOT
     # Get access token for IAM authentication
     #export PGPASSWORD=$(gcloud auth print-access-token)
     # Connect using private IP with IAM authentication
     psql "host=127.0.0.1\
           port=5432 \
           dbname=${var.database_name} \
           user=postgres \
           password=qJoVywHigYGlA1d \
           sslmode=disable" \
           -f "../../../../postgres_resources/lead_mgmt_schema_creation.sql"
   EOT
   environment = {
     CLOUDSQL_INSTANCE = "${var.projectId}:${var.region}:lead_mgmt_adt"
   }
 }

}

/*
 resource "null_resource" "initial_database_setup" {
 triggers = {
   sql_script_hash = filesha256("../../../../postgres_resources/lead_mgmt_schema_creation.sql")
 }
 provisioner "local-exec" {
   command = <<-EOT
     # Get access token for IAM authentication
     export PGPASSWORD=$(gcloud auth print-access-token)
     # Connect using private IP with IAM authentication
     psql "host=127.0.0.1\
           port=5432 \
           dbname=${var.database_name} \
           user=gco-iam-svc-cicd-mbr-bc-np@gcp-prj-cicd-core.iam \
           sslmode=disable" \
           -f "../../../../postgres_resources/lead_mgmt_schema_creation.sql"
   EOT
   environment = {
     CLOUDSQL_INSTANCE = "${var.projectId}:${var.region}:lead_mgmt_adt"
   }
 }

}*/