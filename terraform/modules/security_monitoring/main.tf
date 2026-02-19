# Project Ownership Changes
resource "google_logging_metric" "project_ownership_change" {
  name    = "security/project_ownership_change"
  filter  = "protoPayload.methodName=\"SetIamPolicy\" AND protoPayload.serviceData.policyDelta.bindingDeltas.action=\"ADD\" AND (protoPayload.serviceData.policyDelta.bindingDeltas.role=\"roles/owner\" OR protoPayload.serviceData.policyDelta.bindingDeltas.role=\"roles/editor\")"
  project = var.project_id
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "project_ownership_change_alert" {
  display_name = "Project Ownership Assignment/Change Alert"
  combiner     = "OR"
  conditions {
    display_name = "Project Ownership Change Condition"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.project_ownership_change.name}\" AND resource.type=\"global\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_COUNT"
      }
    }
  }
  notification_channels = var.notification_channels
  project               = var.project_id

  depends_on = [google_logging_metric.project_ownership_change]
}

# Audit Configuration Changes
resource "google_logging_metric" "audit_config_change" {
  name    = "security/audit_config_change"
  filter  = "protoPayload.methodName=\"google.logging.v2.ConfigServiceV2.SetIamPolicy\" OR protoPayload.methodName=\"google.logging.v2.ConfigServiceV2.UpdateSink\""
  project = var.project_id
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "audit_config_change_alert" {
  display_name = "Audit Configuration Change Alert"
  combiner     = "OR"
  conditions {
    display_name = "Audit Configuration Change Condition"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.audit_config_change.name}\" AND resource.type=\"global\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_COUNT"
      }
    }
  }
  notification_channels = var.notification_channels
  project               = var.project_id

  depends_on = [google_logging_metric.audit_config_change]
}

# SQL Instance Configuration Changes
resource "google_logging_metric" "sql_instance_config_change" {
  name    = "security/sql_instance_config_change"
  filter  = "resource.type=\"cloudsql_database\" AND (protoPayload.methodName=\"cloudsql.instances.update\" OR protoPayload.methodName=\"cloudsql.instances.patch\")"
  project = var.project_id
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "sql_instance_config_change_alert" {
  display_name = "SQL Instance Configuration Change Alert"
  combiner     = "OR"
  conditions {
    display_name = "SQL Instance Config Change Condition"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.sql_instance_config_change.name}\" AND resource.type=\"cloudsql_database\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_COUNT"
      }
    }
  }
  notification_channels = var.notification_channels
  project               = var.project_id

  depends_on = [google_logging_metric.sql_instance_config_change]
}

# Custom Role Changes
resource "google_logging_metric" "custom_role_change" {
  name    = "security/custom_role_change"
  filter  = "resource.type=\"iam_role\" AND (protoPayload.methodName=\"google.iam.admin.v1.CreateRole\" OR protoPayload.methodName=\"google.iam.admin.v1.DeleteRole\" OR protoPayload.methodName=\"google.iam.admin.v1.UpdateRole\")"
  project = var.project_id
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "custom_role_change_alert" {
  display_name = "Custom Role Change Alert"
  combiner     = "OR"
  conditions {
    display_name = "Custom Role Change Condition"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.custom_role_change.name}\" AND resource.type=\"global\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_COUNT"
      }
    }
  }
  notification_channels = var.notification_channels
  project               = var.project_id

  depends_on = [google_logging_metric.custom_role_change]
}

# Cloud Storage IAM Permission Changes
resource "google_logging_metric" "gcs_iam_change" {
  name    = "security/gcs_iam_change"
  filter  = "resource.type=\"gcs_bucket\" AND protoPayload.methodName=\"storage.setIamPermissions\""
  project = var.project_id
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "gcs_iam_change_alert" {
  display_name = "Cloud Storage IAM Permission Change Alert"
  combiner     = "OR"
  conditions {
    display_name = "GCS IAM Change Condition"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.gcs_iam_change.name}\" AND resource.type=\"gcs_bucket\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_COUNT"
      }
    }
  }
  notification_channels = var.notification_channels
  project               = var.project_id

  depends_on = [google_logging_metric.gcs_iam_change]
}
