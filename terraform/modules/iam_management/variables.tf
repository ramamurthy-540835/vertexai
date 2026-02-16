variable "project_id" {
  description = "The ID of the GCP Project"
  type        = string
}

variable "bucket_iam_bindings" {
  description = "List of bucket IAM bindings"
  type = list(object({
    bucket = string
    role   = string
    member = string
  }))
  default = []
}
