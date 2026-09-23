variable "project_id" {
  type        = string
  description = "GCP project id"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "service_name" {
  type    = string
  default = "equity-research"
}

variable "image_tag" {
  type        = string
  description = "Tag of the image pushed by Cloud Build (e.g. the git commit)"
  default     = "latest"
}

variable "deploy_service" {
  type        = bool
  description = "false for the first apply (repo + service account only), before an image exists"
  default     = true
}

variable "chat_model" {
  type    = string
  default = "gemini-2.5-flash"
}

variable "sec_user_agent" {
  type        = string
  description = "Name and email SEC requires in the User-Agent header"
}

variable "invoker_members" {
  type        = list(string)
  description = "Principals allowed to call the service, e.g. [\"user:you@example.com\"]"
}

variable "max_instances" {
  type    = number
  default = 2 # cost guard
}
