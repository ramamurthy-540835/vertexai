# ─────────────────────────────────────────────────────────────
# Eventarc Trigger
# ─────────────────────────────────────────────────────────────
# NOTE: previously depended on google_service_account_iam_member.pubsub_token_creator,
# which has been removed from this module (that IAM binding is now raised
# separately, from another project/state — see the consolidated
# pubsub_iam_bindings.tf). That binding must exist before this trigger's
# auto-created push subscription can mint OIDC tokens correctly, but
# Terraform can no longer express that dependency across states, so apply
# order must be handled operationally: IAM bindings first, then this module.
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
}
