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
  username = var.iam_user
  sslmode  = "disable"
}

 resource "null_resource" "initial_database_setup" {
 triggers = {
   sql_script_hash = filesha256("../../../../postgres_resources/lead_mgmt_schema_creation.sql")
 }
 provisioner "local-exec" {
   
   command = <<-EOT
     export SCHEMA_NAME=${var.schema_name}
     export DATABASE_NAME=${var.database_name}
     export IAM_USER=${var.iam_user}
     export NEW_IAM_USER="gco-iam-svc-lead-mgmt-bc-qat@p-601-np-bcleadsmgmt-qat.iam"

     envsubst < "../../../../postgres_resources/lead_mgmt_schema_creation.sql" > /tmp/lead_mgmt_schema_creation.sql
     # Connect using private IP with IAM authentication
     psql "host=127.0.0.1\
           port=5432 \
           dbname=${var.database_name} \
           user=postgres \
           password=hu@OX655@-9_ \
           sslmode=disable" \
           -f /tmp/lead_mgmt_schema_creation.sql
   EOT
   environment = {
     CLOUDSQL_INSTANCE = "${var.projectId}:${var.region}:${var.instance}"
   }
 }

}

 resource "null_resource" "table_creation" {
 depends_on = [null_resource.initial_database_setup]
 triggers = {
   sql_script_hash = filesha256("../../../../postgres_resources/costco_db_ddl.sql")
 }
 provisioner "local-exec" {
   command = <<-EOT
     # Get access token for IAM authentication
     export PGPASSWORD=$(gcloud auth print-access-token)
     export SCHEMA_NAME=${var.schema_name}
     export DATABASE_NAME=${var.database_name}
     export IAM_USER=${var.iam_user}
     export NEW_IAM_USER="gco-iam-svc-lead-mgmt-bc-qat@p-601-np-bcleadsmgmt-qat.iam"


     envsubst < "../../../../postgres_resources/costco_db_ddl.sql" > /tmp/costco_db_ddl.sql

     # Connect using private IP with IAM authentication
     psql "host=127.0.0.1\
           port=5432 \
           dbname=${var.database_name} \
           user=${var.iam_user} \
           sslmode=disable" \
           -f /tmp/costco_db_ddl.sql
   EOT
   environment = {
     CLOUDSQL_INSTANCE = "${var.projectId}:${var.region}:${var.instance}"
   }
 }

}

resource "null_resource" "data_load" {
 depends_on = [null_resource.table_creation]
 triggers = {
   sql_script_hash = filesha256("../../../../postgres_resources/costco_db_dml.sql")
 }
 provisioner "local-exec" {
   command = <<-EOT
     # Get access token for IAM authentication
     export PGPASSWORD=$(gcloud auth print-access-token)
     export SCHEMA_NAME=${var.schema_name}

     envsubst < "../../../../postgres_resources/costco_db_dml.sql" > /tmp/costco_db_dml.sql

     # Connect using private IP with IAM authentication
     psql "host=127.0.0.1\
           port=5432 \
           dbname=${var.database_name} \
           user=${var.iam_user} \
           sslmode=disable" \
           -f /tmp/costco_db_dml.sql
   EOT
   environment = {
     CLOUDSQL_INSTANCE = "${var.projectId}:${var.region}:${var.instance}"
   }
 }

}
resource "null_resource" "alter_table" {
 depends_on = [null_resource.table_creation]
 triggers = {
   sql_script_hash = filesha256("../../../../postgres_resources/costco_alter_table.sql")
 }
 provisioner "local-exec" {
   command = <<-EOT
     # Get access token for IAM authentication
     export PGPASSWORD=$(gcloud auth print-access-token)
     export SCHEMA_NAME=${var.schema_name}

     envsubst < "../../../../postgres_resources/costco_alter_table.sql" > /tmp/costco_alter_table.sql

     # Connect using private IP with IAM authentication
     psql "host=127.0.0.1\
           port=5432 \
           dbname=${var.database_name} \
           user=${var.iam_user} \
           sslmode=disable" \
           -f /tmp/costco_alter_table.sql
   EOT
   environment = {
     CLOUDSQL_INSTANCE = "${var.projectId}:${var.region}:${var.instance}"
   }
 }

}