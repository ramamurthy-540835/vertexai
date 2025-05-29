output "id" {
  description = "The ID of the created secret"
  value       = google_secret_manager_secret.this.id
}

output "name" {
  description = "The name of the created secret"
  value       = google_secret_manager_secret.this.name
}

output "version" {
  description = "The version of the created secret"
  value       = google_secret_manager_secret_version.this.name
}
