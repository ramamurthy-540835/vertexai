variable "topic_name" {
  description = "The name of the Pub/Sub topic."
  type        = string
}

variable "scheduler_name" {
  description = "The name of the Cloud Scheduler job."
  type        = string
}

variable "schedule" {
  description = "The cron schedule for the Cloud Scheduler job (e.g., '0 2 * * *' for 2:00 AM UTC)."
  type        = string
}

variable "time_zone" {
  description = "The time zone in which the schedule is specified (e.g., 'UTC')."
  type        = string
}

variable "attempt_deadline" {
  description = "The deadline for job attempts, e.g., '320s'."
  type        = string
}

variable "data" {
  description = "The data to send to the Pub/Sub topic (must be a string, will be base64 encoded)."
  type        = string
}