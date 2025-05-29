output "pubsub_topic_id" {
  description = "ID of the created Pub/Sub topic."
  value       = google_pubsub_topic.snow_sync_trigger.id
}

#output "scheduler_job_name" {
#  description = "Name of the created Cloud Scheduler job."
#  value       = google_cloud_scheduler_job.snow_sync_scheduler.name
#}