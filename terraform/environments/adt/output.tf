output "bucket_name" {
  description = "Fully qualified cs bucket name"
  value       = google_storage_bucket.this.name
}
