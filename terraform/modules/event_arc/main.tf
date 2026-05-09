# ─────────────────────────────────────────────────────────────
# Eventarc trigger — Pub/Sub source → Cloud Workflow destination
#
# This module subscribes to an existing Pub/Sub topic (managed by the
# pubsub_gcs_trigger module) and routes messages to a Cloud Workflow.
#
# Eventarc auto-creates and manages its own push subscription on the
# topic — we don't manage that subscription directly.
#
# Architecture:
#   GCS upload (folder-scoped notification)
#     → Pub/Sub topic (managed by pubsub_gcs_trigger module)
#     → Eventarc trigger (this module, type=messagePublished)
#     → Cloud Workflow
# ─────────────────────────────────────────────────────────────

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

  # IAM bindings must be in place before the trigger can be created
  depends_on = [
    google_project_iam_member.trigger_workflow_invoker,
    google_project_iam_member.trigger_event_receiver,
    google_service_account_iam_member.pubsub_token_creator,
  ]
}

# ─────────────────────────────────────────────────────────────
# IAM bindings the trigger SA needs
# ─────────────────────────────────────────────────────────────

# Trigger SA must be able to invoke the destination workflow
resource "google_project_iam_member" "trigger_workflow_invoker" {
  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = "serviceAccount:${var.service_account_email}"
}

# Trigger SA must be able to receive Eventarc events
resource "google_project_iam_member" "trigger_event_receiver" {
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${var.service_account_email}"
}

# Eventarc's auto-created push subscription uses OIDC tokens to authenticate
# to the workflow. The Pub/Sub service agent mints those tokens by impersonating
# the trigger SA — this binding is what makes that allowed.
resource "google_service_account_iam_member" "pubsub_token_creator" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${var.service_account_email}"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}