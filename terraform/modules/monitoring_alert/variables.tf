variable "display_name" {
  description = "Display name for the email notification channel."
  type        = string
  default     = "Lead_mgmt - Email Alert"
}

variable "type" {
  description = "Type of the notification channel (e.g., 'email')."
  type        = string
  default     = "email"
}

variable "email_address" {
  description = "The email address to receive alert notifications."
  type        = string
  default     = "membership_mit_team@costco.com"
}

variable "policy_display_name" {
  description = "Display name for the alert policy."
  type        = string
  default     = "Cloud Run Job Failure Alert"
}

variable "first_condition_display_name" {
  description = "Display name for the first alert condition (match-job failure)."
  type        = string
  default     = "Match Job Failure Condition"
}

variable "second_condition_display_name" {
  description = "Display name for the second alert condition (snow-sync-job failure)."
  type        = string
  default     = "SNOW Sync Job Failure Condition"
}
