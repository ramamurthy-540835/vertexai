variable "project_id" {
  description = "The project ID to host the network in"
  type        = string
}

variable "notification_channels" {
  description = "The notification channels to send alerts to"
  type        = list(string)
  default     = []
}
