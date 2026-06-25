resource "google_compute_region_network_endpoint_group" "cloud_run_neg" {
  name                  = "${var.cloud_run_service_name}-neg"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = var.cloud_run_service_name
  }
}

resource "google_compute_backend_service" "this" {
  name                  = var.backend_name != "" ? var.backend_name : "${var.cloud_run_service_name}-backend"
  project               = var.project_id
  protocol              = "HTTPS"
  load_balancing_scheme = var.load_balancing_scheme

  backend {
    group = google_compute_region_network_endpoint_group.cloud_run_neg.id
  }

  log_config {
    enable      = var.enable_logging
    sample_rate = var.log_sample_rate
  }
}