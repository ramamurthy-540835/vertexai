resource "google_project_service" "apis" {
  for_each = toset(var.services)

  project = var.project_id
  service = each.value

  disable_on_destroy = false
}

resource "google_service_account" "main" {
  account_id   = var.service_account_id
  display_name = var.service_account_display_name
  project      = var.project_id
}
