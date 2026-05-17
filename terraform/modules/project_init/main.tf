resource "google_project_service" "apis" {
  for_each = toset(var.services)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "main" {
  account_id   = var.service_account_id
  display_name = var.service_account_display_name
  project      = var.project_id
}

# -------------------------------------------------------
# Force-create Google-managed service identities
# -------------------------------------------------------
resource "null_resource" "create_service_identity" {
  for_each = toset([
    "ml.googleapis.com",
    "aiplatform.googleapis.com",
  ])

  triggers = {
    project_id = var.project_id
    service    = each.value
  }

  provisioner "local-exec" {
    command     = "gcloud beta services identity create --service=${each.value} --project=${var.project_id}"
    interpreter = ["/bin/bash", "-c"]
  }

  depends_on = [
    google_project_service.apis
  ]
}