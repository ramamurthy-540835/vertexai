variable "project_id" {
  description = "The ID of the GCP Project"
  type        = string
}

variable "bucket_name" {
  description = "The name of the bucket"
  type        = string
}

variable "location" {
  description = "The geographic location for the bucket"
  type        = string
}

variable "labels" {
  description = "Resource labels"
  type        = map(string)
  default     = {}
}

variable "versioning_enabled" {
  description = "Whether to enable versioning"
  type        = bool
  default     = true
}

variable "soft_delete_retention_seconds" {
  description = "Soft delete retention duration in seconds"
  type        = number
  default     = 604800 # 7 days
}

variable "log_bucket" {
  description = "The name of the bucket to write logs to"
  type        = string
  default     = null
}

variable "log_object_prefix" {
  description = "The prefix for log objects"
  type        = string
  default     = null
}

variable "grant_logging_permissions" {
  description = "Whether to grant GCS service agent logging permissions to this bucket"
  type        = bool
  default     = false
}

variable "lifecycle_rules" {
  description = "List of lifecycle rules"
  type = list(object({
    action = object({
      type          = string
      storage_class = optional(string)
    })
    condition = object({
      age                        = optional(number)
      created_before             = optional(string)
      with_state                 = optional(string)
      matches_storage_class      = optional(list(string))
      num_newer_versions         = optional(number)
      days_since_noncurrent_time = optional(number)
    })
  }))
  default = [
    {
      action = { type = "Delete" }
      condition = { days_since_noncurrent_time = 7 }
    },
    {
      action = { type = "Delete" }
      condition = { 
        with_state         = "ARCHIVED"
        num_newer_versions = 3
      }
    }
  ]
}
