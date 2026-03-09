output "workflow_id" {
  description = "The unique identifier for the workflow"
  value       = google_workflows_workflow.snow_sync_workflow.id
}

output "workflow_name" {
  description = "The name of the workflow"
  value       = google_workflows_workflow.snow_sync_workflow.name
}

output "workflow_state" {
  description = "The current state of the workflow (e.g., ACTIVE)"
  value       = google_workflows_workflow.snow_sync_workflow.state
}

output "workflow_service_account" {
  description = "Service account used by the workflow"
  value       = google_workflows_workflow.snow_sync_workflow.service_account
}

output "workflow_uri" {
  description = "The URI to execute the workflow"
  value       = "https://workflowexecutions.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/workflows/${google_workflows_workflow.snow_sync_workflow.name}/executions"
}

