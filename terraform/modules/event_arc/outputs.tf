output "eventarc_trigger_name" {
  value = google_eventarc_trigger.gcs_workflow_trigger.name
}

output "eventarc_trigger_id" {
  value = google_eventarc_trigger.gcs_workflow_trigger.id
}