# Creating secret
resource "google_secret_manager_secret" "this" {
  project   = var.project
  secret_id = var.secret_id

 replication {
    user_managed {
      replicas {
        location = "us-central1"
      }
    }
  }
}


# Creating secret version
resource "google_secret_manager_secret_version" "this" {
  secret = google_secret_manager_secret.this.id
  secret_data = var.secret_value
}
