resource "google_pubsub_topic" "snow_sync_trigger" {
  name = var.topic_name
}

resource "google_cloud_scheduler_job" "snow_sync_scheduler" {
  name             = var.scheduler_name
  schedule         = var.schedule # 2:00 AM UTC
  time_zone        = var.time_zone
  attempt_deadline = var.attempt_deadline

  pubsub_target {
    topic_name = google_pubsub_topic.snow_sync_trigger.id
    data       = base64encode(var.data)
  }
}
