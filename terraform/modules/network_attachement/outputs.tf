output "network_attachment_self_link" {
  description = "Self-link of the PSC network attachment (pass this to Vertex AI API calls)."
  value       = google_compute_network_attachment.psc_network_attachment.self_link
}

output "network_attachment_id" {
  description = "Full resource ID of the network attachment."
  value       = google_compute_network_attachment.psc_network_attachment.id
}

output "subnet_self_link" {
  value = data.google_compute_subnetwork.psc_subnet.self_link
}