output "bucket_name" {
  description = "Fully qualified cs bucket name"
  value       = google_storage_bucket.this.name
}

output "scheduler_job_name" {
  value = google_cloud_scheduler_job.snow_sync_scheduler.name
}
