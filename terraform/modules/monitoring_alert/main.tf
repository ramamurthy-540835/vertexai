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
      filter          = "metric.type=\"run.googleapis.com/job/completed_execution_count\" AND resource.type=\"cloud_run_job\" AND resource.label.\"job_name\"=\"lead-match-job\" AND metric.label.\"result\"=\"failed\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_SUM"
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
      duration        = "0s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_SUM"
      }
      trigger {
        count = 1
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.lead_mgmt_email.id]
}
/*
resource "google_logging_metric" "project_ownership_metric" {
  name   = "project-ownership-changes"
  filter = "resource.type=\"project\" AND protoPayload.serviceName=\"cloudresourcemanager.googleapis.com\" AND (protoPayload.methodName=\"SetIamPolicy\" OR protoPayload.methodName=\"ProjectOwnership\") AND (protoPayload.serviceData.policyDelta.bindingDeltas.role=\"roles/owner\")"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

#Project Ownership Changes alert
resource "google_monitoring_alert_policy" "project_ownership_alert" {
  display_name = "Alert: Project Ownership Changes"
  combiner     = "OR"
  conditions {
    display_name = "Ownership Change Detected"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.project_ownership_metric.name}\" AND resource.type=\"global\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
    }
  }
  notification_channels = [google_monitoring_notification_channel.lead_mgmt_email.id]
}


resource "google_logging_metric" "audit_config_metric" {
  name   = "audit-config-changes"
  filter = "protoPayload.methodName=\"SetIamPolicy\" AND protoPayload.serviceData.policyDelta.auditConfigDeltas:*"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

#Configuration Change Alert
resource "google_monitoring_alert_policy" "audit_config_alert" {
  display_name = "Alert: Audit Config Changes"
  combiner     = "OR"
  conditions {
    display_name = "Audit Config Change Detected"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.audit_config_metric.name}\" AND resource.type=\"global\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
    }
  }
  notification_channels = [google_monitoring_notification_channel.lead_mgmt_email.id]
}

resource "google_logging_metric" "gcs_iam_metric" {
  name   = "gcs-iam-changes"
  filter = "resource.type=\"gcs_bucket\" AND protoPayload.methodName=\"storage.setIamPermissions\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

#Monitors changes to IAM permissions on GCS buckets.
resource "google_monitoring_alert_policy" "gcs_iam_alert" {
  display_name = "Alert: GCS IAM Permission Changes"
  combiner     = "OR"
  conditions {
    display_name = "GCS IAM Change Detected"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.gcs_iam_metric.name}\" AND resource.type=\"gcs_bucket\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
    }
  }
  notification_channels = [google_monitoring_notification_channel.lead_mgmt_email.id]
}

#Monitors configuration updates to SQL database instances
resource "google_logging_metric" "sql_config_metric" {
  name   = "sql-instance-config-changes"
  filter = "protoPayload.methodName=\"cloudsql.instances.update\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}


resource "google_monitoring_alert_policy" "sql_config_alert" {
  display_name = "Alert: SQL Instance Configuration Changes"
  combiner     = "OR"
  conditions {
    display_name = "SQL Config Change Detected"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.sql_config_metric.name}\" AND resource.type=\"cloudsql_database\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
    }
  }
  notification_channels = [google_monitoring_notification_channel.lead_mgmt_email.id]
}

#Monitors the creation, deletion, or modification of custom IAM roles
resource "google_logging_metric" "custom_role_metric" {
  name   = "custom-role-changes"
  filter = "resource.type=\"iam_role\" AND (protoPayload.methodName=\"google.iam.admin.v1.CreateRole\" OR protoPayload.methodName=\"google.iam.admin.v1.DeleteRole\" OR protoPayload.methodName=\"google.iam.admin.v1.UpdateRole\")"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_alert_policy" "custom_role_alert" {
  display_name = "Alert: Custom IAM Role Changes"
  combiner     = "OR"
  conditions {
    display_name = "Custom Role Change Detected"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.custom_role_metric.name}\" AND resource.type=\"global\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
    }
  }
  notification_channels = [google_monitoring_notification_channel.lead_mgmt_email.id]
} */