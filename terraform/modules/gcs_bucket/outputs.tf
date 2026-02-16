output "bucket_name" {
  description = "The name of the bucket"
  value       = google_storage_bucket.this.name
}

output "bucket_url" {
  description = "The URL of the bucket"
  value       = google_storage_bucket.this.url
}
