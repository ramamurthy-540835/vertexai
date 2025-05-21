variable "location" {
  description = "The location of the repository"
  type        = string
}

variable "repository_id" {
  description = "The name of the repository"
  type        = string
}

variable "description" {
  description = "The description of the repository"
  type        = string
  default     = ""
}

variable "format" {
  description = "The format of packages in the repository"
  type        = string
  default     = "DOCKER"
}
