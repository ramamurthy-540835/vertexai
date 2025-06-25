output "instance_name" {
  description = "The name of the Cloud SQL instance"
  value       = google_sql_database_instance.this.name
}

output "instance_connection_name" {
  description = "The connection name of the instance to be used in connection strings"
  value       = google_sql_database_instance.this.connection_name
}

output "instance_self_link" {
  description = "The URI of the created resource"
  value       = google_sql_database_instance.this.self_link
}

output "instance_first_ip_address" {
  description = "The first assigned IPv4 address of the instance"
  value       = google_sql_database_instance.this.ip_address[0].ip_address
}

output "postgresql_connection" {
  description = "PostgreSQL connection details for provider configuration"
  value = {
    host         = google_sql_database_instance.this.private_ip_address
    port         = 5432
    database     = var.database_name
    username     = trimsuffix(var.service_account, ".gserviceaccount.com")
    sslmode      = "require"
    ready_marker = null_resource.postgresql_ready.id
  }
  sensitive = false
}
