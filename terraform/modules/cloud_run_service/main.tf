resource "google_cloud_run_v2_service" "this" {
  name     = var.service_name
  project  = var.project_id
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account = var.service_account_email
    timeout         = "${var.timeout_seconds}s"
    labels = {
    image-tag = var.service_image_tag
  }

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    vpc_access {
      network_interfaces {
        network    = var.network
        subnetwork = var.subnet
      }
      egress = "ALL_TRAFFIC"
    }

    containers {
      image = "us-docker.pkg.dev/gcp-prj-images/gcp-gar-repo-mbrshp/cloud-run-service:${var.service_image_tag}"

      resources {
        limits = {
          memory = var.memory
          cpu    = var.cpu
        }
      }

      dynamic "env" {
        for_each = var.env_vars
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
}

resource "google_cloud_scheduler_job" "health_check" {
  count    = var.enable_health_scheduler ? 1 : 0
  name     = "${var.service_name}-scheduler"
  project  = var.project_id
  region   = var.region
  schedule = var.scheduler_cron

  http_target {
    uri         = "${google_cloud_run_v2_service.this.uri}/health"
    http_method = "GET"

    oidc_token {
      service_account_email = var.service_account_email
    }
  }
}