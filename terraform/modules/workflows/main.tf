# --- Enable APIs ---
resource "google_project_service" "workflows_api" {
  project = var.project_id
  service = "workflows.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "run_api" {
  project = var.project_id
  service = "run.googleapis.com"
  disable_on_destroy = false
}

# --- Service Account for Workflow ---
# Using existing service account passed via variable

# --- Deploy the Workflow ---
resource "google_workflows_workflow" "snow_sync_workflow" {
  project = var.project_id
  name    = var.workflow_name
  region  = var.workflow_region
  description = var.workflow_description

  # Associate the service account with the workflow
  service_account = var.service_account_email

  # Import the workflow definition from the external YAML file
  source_contents = file(var.workflow_path)

  # Ensure APIs and service account are ready before deploying workflow
  depends_on = [
    google_project_service.workflows_api,
    google_project_service.run_api
  ]

  # Optional: For production, set to true to prevent accidental deletion
  deletion_protection = false
}
