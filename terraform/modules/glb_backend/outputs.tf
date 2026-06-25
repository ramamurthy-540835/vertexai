output "backend_service_id" {
  description = "Fully qualified resource ID of the backend service"
  value       = google_compute_backend_service.this.id
}

output "backend_service_name" {
  description = "Name of the backend service"
  value       = google_compute_backend_service.this.name
}

output "backend_service_self_link" {
  description = "Self link of the backend service, for use in URL maps"
  value       = google_compute_backend_service.this.self_link
}

output "neg_id" {
  description = "Fully qualified resource ID of the serverless NEG"
  value       = google_compute_region_network_endpoint_group.cloud_run_neg.id
}

output "neg_self_link" {
  description = "Self link of the serverless NEG"
  value       = google_compute_region_network_endpoint_group.cloud_run_neg.self_link
}