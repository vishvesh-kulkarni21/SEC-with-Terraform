output "service_url" {
  value = var.deploy_service ? google_cloud_run_v2_service.app[0].uri : null
}

output "image" {
  value = local.image
}

output "runtime_service_account" {
  value = google_service_account.runtime.email
}

output "build_service_account" {
  value = google_service_account.builder.id
}
