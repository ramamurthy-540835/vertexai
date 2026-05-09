data "google_project" "project" {
  project_id = var.project_id
}

# ─────────────────────────────────────────────────────────────
# Eventarc Trigger
# ─────────────────────────────────────────────────────────────
resource "google_eventarc_trigger" "gcs_workflow_trigger" {
  name            = var.trigger_name
  location        = var.location
  project         = var.project_id
  service_account = var.service_account_email

  # Event type: a message was published to the source Pub/Sub topic.
  # Eventarc doesn't care that GCS is the upstream publisher — it just
  # listens for any message landing on the topic.
  matching_criteria {
    attribute = "type"
    value     = "google.cloud.pubsub.topic.v1.messagePublished"
  }

  # Subscribe to the existing Pub/Sub topic (managed by the
  # pubsub_gcs_trigger module). Eventarc creates its own push
  # subscription on this topic.
  transport {
    pubsub {
      topic = var.pubsub_topic_id
    }
  }

  destination {
    workflow = "projects/${var.project_id}/locations/${var.workflow_location}/workflows/${var.workflow_name}"
  }

  # Token-creator binding must exist before trigger creation
  depends_on = [google_service_account_iam_member.pubsub_token_creator]
}

# ─────────────────────────────────────────────────────────────
# IAM binding specific to this trigger
# ─────────────────────────────────────────────────────────────
# Eventarc's auto-created push subscription uses OIDC tokens to authenticate
# to the workflow. The Pub/Sub service agent mints those tokens by impersonating
# the trigger SA — this binding is what makes that allowed.
#
# This binding is per-service-account (not project-wide), so it lives here
# rather than in project_init even when the project_init grants the
# project-level Eventarc/Workflows roles.
resource "google_service_account_iam_member" "pubsub_token_creator" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${var.service_account_email}"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}