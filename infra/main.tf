# agent-infra: GCP infrastructure for the equity research agent.
#
# Creates: required APIs, an Artifact Registry repo, a least-privilege runtime service
# account, and a private Cloud Run service (IAM-authenticated invokers only).
# Model access is Vertex AI through the service account: no API keys anywhere.

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "aiplatform.googleapis.com",
    "logging.googleapis.com",
    "iam.googleapis.com",
  ]
  image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/${var.service_name}:${var.image_tag}"
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  repository_id = "agent-images"
  location      = var.region
  format        = "DOCKER"
  description   = "Container images for the equity research agent"
  depends_on    = [google_project_service.apis]
}

# Runtime identity: only what the service needs (call Vertex AI, write logs).
resource "google_service_account" "runtime" {
  account_id   = "${var.service_name}-run"
  display_name = "Equity research agent (Cloud Run runtime)"
}

resource "google_project_iam_member" "runtime_roles" {
  for_each = toset(["roles/aiplatform.user", "roles/logging.logWriter"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_cloud_run_v2_service" "app" {
  count               = var.deploy_service ? 1 : 0
  name                = var.service_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account                  = google_service_account.runtime.email
    timeout                          = "300s" # a full debate takes ~2 minutes
    max_instance_request_concurrency = 4

    scaling {
      min_instance_count = 0 # scale to zero: no cost when idle
      max_instance_count = var.max_instances
    }

    containers {
      image = local.image
      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
        cpu_idle = true
      }
      env {
        name  = "VERTEX_PROJECT"
        value = var.project_id
      }
      env {
        name  = "VERTEX_LOCATION"
        value = var.region
      }
      env {
        name  = "CHAT_MODEL"
        value = var.chat_model
      }
      env {
        name  = "SEC_USER_AGENT"
        value = var.sec_user_agent
      }
    }
  }

  depends_on = [google_project_service.apis, google_project_iam_member.runtime_roles]
}

# Private service: only listed principals can invoke it (no allUsers).
resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = var.deploy_service ? toset(var.invoker_members) : toset([])
  name     = google_cloud_run_v2_service.app[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = each.value
}

# Build identity for Cloud Build: push images, read the uploaded source, write build logs.
# A dedicated account instead of the Compute Engine default (which new projects may not
# have, and which is far broader than a build needs).
resource "google_service_account" "builder" {
  account_id   = "${var.service_name}-build"
  display_name = "Equity research agent (Cloud Build)"
}

resource "google_project_iam_member" "builder_roles" {
  for_each = toset(["roles/artifactregistry.writer", "roles/logging.logWriter", "roles/storage.objectViewer"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.builder.email}"
}
