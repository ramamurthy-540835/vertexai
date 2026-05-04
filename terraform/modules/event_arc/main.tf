resource "google_eventarc_trigger" "gcs_csv_trigger" {
  name     = var.trigger_name
  location = var.region

  # Event filters
  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }

  matching_criteria {
    attribute = "bucket"
    value     = var.bucket_name
  }

  # Filter for folder (prefix)
  matching_criteria {
    attribute = "objectNamePrefix"
    value     = var.path
  }

  # Filter for CSV files
  matching_criteria {
    attribute = "objectNameSuffix"
    value     = ".csv"
  }

  # Destination: Workflows
  destination {
    workflow = "projects/${var.project_id}/locations/${var.region}/workflows/${var.workflow_name}"
  }

  service_account = var.service_account_email
}