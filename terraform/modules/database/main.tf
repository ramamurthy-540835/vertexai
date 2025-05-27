resource "google_sql_database_instance" "this" {
  name             = var.instance_name
  database_version = var.version
  region           = var.region

  settings {
    # Second-generation instance tiers are based on the machine
    # type. See argument reference below.
    tier = var.tier
    edition = var.edition
    data_cache_config {
        data_cache_enabled = true
    }
     backup_configuration {
      enabled = true
      binary_log_enabled = true
    }
    availability_type = var.availability_type
    activation_policy = var.activation_policy
    deletion_protection_enabled = true
    disk_autoresize = true
    disk_size = var.disk_size
    insights_config {
    query_insights_enabled = true
    }
  }
}
