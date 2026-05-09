output "topic_id" {
  description = "Fully-qualified Pub/Sub topic ID"
  value       = google_pubsub_topic.this.id
}

output "topic_name" {
  description = "Pub/Sub topic name"
  value       = google_pubsub_topic.this.name
}

output "deadletter_topic_id" {
  description = "Dead-letter Pub/Sub topic ID"
  value       = google_pubsub_topic.deadletter.id
}

output "deadletter_subscription_id" {
  description = "Dead-letter pull subscription ID"
  value       = google_pubsub_subscription.deadletter_pull.id
}

output "notification_id" {
  description = "GCS bucket notification ID"
  value       = google_storage_notification.this.notification_id
}

output "watched_folder" {
  description = "GCS folder prefix being watched"
  value       = var.folder_prefix
}
