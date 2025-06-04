output "notification_channel_id" {
  description = "ID of the created email notification channel."
  value       = google_monitoring_notification_channel.lead_mgmt_email.id
}

output "alert_policy_id" {
  description = "ID of the created Cloud Run job failure alert policy."
  value       = google_monitoring_alert_policy.cloud_run_job_failure.id
}