resource "google_monitoring_notification_channel" "lead_mgmt_email" {
  display_name = var.display_name
  type         = var.type

  labels = {
    email_address = var.email_address
  }
}

resource "google_monitoring_alert_policy" "cloud_run_job_failure" {
  display_name = var.policy_display_name
  combiner     = "OR"
  enabled      = true

  conditions {
    display_name = var.first_condition_display_name

    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/job/completed_execution_count\" AND resource.type=\"cloud_run_job\" AND resource.label.\"job_name\"=\"match-job\" AND metric.label.\"result\"=\"failed\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "60s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_RATE"
      }
      trigger {
        count = 1
      }
    }
  }

  conditions {
    display_name = var.second_condition_display_name

    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/job/completed_execution_count\" AND resource.type=\"cloud_run_job\" AND resource.label.\"job_name\"=\"snow-sync-job\" AND metric.label.\"result\"=\"failed\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "60s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_RATE"
      }
      trigger {
        count = 1
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.lead_mgmt_email.id]
}
