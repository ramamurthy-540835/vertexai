output "eventarc_trigger_id" {
  description = "ID of the Eventarc trigger"
  value       = google_eventarc_trigger.gcs_csv_trigger.id
}

output "eventarc_trigger_name" {
  description = "Name of the Eventarc trigger"
  value       = google_eventarc_trigger.gcs_csv_trigger.name
}

output "eventarc_trigger_location" {
  description = "Region of the Eventarc trigger"
  value       = google_eventarc_trigger.gcs_csv_trigger.location
}

output "workflow_target" {
  description = "Workflow triggered by Eventarc"
  value       = google_eventarc_trigger.gcs_csv_trigger.destination[0].workflow
}