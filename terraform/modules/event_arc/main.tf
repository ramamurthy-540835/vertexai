resource "google_eventarc_trigger" "gcs_csv_trigger" {
  name     = var.trigger_name
  location = var.region

  # Event filters
  event_filters {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }

  event_filters {
    attribute = "bucket"
    value     = var.bucket_name
  }

  # Filter for folder (prefix)
  event_filters {
    attribute = "objectNamePrefix"
    value     = var.path
  }

  # Filter for CSV files
  event_filters {
    attribute = "objectNameSuffix"
    value     = ".csv"
  }

  # Destination: Workflows
  destination {
    workflow = "projects/${var.project_id}/locations/${var.region}/workflows/${var.workflow_name}"
  }

  service_account = var.service_account_email
}