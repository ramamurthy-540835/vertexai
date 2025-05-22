# Creating secret
resource "google_secret_manager_secret" "this" {
  project   = var.project_id
  secret_id = var.secret_id

  replication {
    automatic = true
  }
}

# Creating secret version with service account key
resource "google_secret_manager_secret_version" "this" {
  secret = google_secret_manager_secret.this.id

  secret_data = base64decode(google_service_account_key.this.private_key)
}
