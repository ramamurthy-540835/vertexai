output "service_name" {
  description = "Name of the deployed Cloud Run service"
  value       = google_cloud_run_v2_service.this.name
}

output "service_url" {
  description = "URL of the deployed Cloud Run service"
  value       = google_cloud_run_v2_service.this.uri
}

output "service_id" {
  description = "Fully qualified resource ID of the Cloud Run service"
  value       = google_cloud_run_v2_service.this.id
}

output "latest_revision" {
  description = "Name of the latest ready revision"
  value       = google_cloud_run_v2_service.this.latest_ready_revision
}

output "scheduler_job_name" {
  description = "Name of the health-check scheduler job, if created"
  value       = var.enable_health_scheduler ? google_cloud_scheduler_job.health_check[0].name : null
}