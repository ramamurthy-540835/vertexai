# ------------------------------------
# Reference existing subnet
# ------------------------------------

data "google_compute_subnetwork" "psc_subnet" {
  project = var.vpc_project_id
  name    = var.subnet_name
  region  = var.region
}

# ------------------------------------
# PSC Network Attachment
# ------------------------------------

resource "google_compute_network_attachment" "psc_network_attachment" {
  project               = var.project_id
  name                  = var.network_attachment_name
  region                = var.region
  connection_preference = "ACCEPT_AUTOMATIC"

  subnetworks = [
    data.google_compute_subnetwork.psc_subnet.self_link
  ]
}
