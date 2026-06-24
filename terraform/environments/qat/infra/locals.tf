locals {
  env_files = {
    service = "${path.module}/../../../../cloud_run_service/service_qat.properties"
    snow_job     = "${path.module}/../../../../lead_management_job/snow_sync_config/app_qat.properties"
    lead_match_job     = "${path.module}/../../../../lead_management_job/lead_match_config/lead_matching_config_qat.properties"
  }

  parsed_env_vars = {
    for key, path_ in local.env_files : key => {
      for line in [
        for l in split("\n", file(path_)) : trimspace(l)
        if trimspace(l) != "" && !startswith(trimspace(l), "#")
      ] :
      split("=", line)[0] => join("=", slice(split("=", line), 1, length(split("=", line))))
    }
  }
}