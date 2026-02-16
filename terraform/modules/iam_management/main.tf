resource "google_storage_bucket_iam_member" "bucket_bindings" {
  for_each = { for idx, binding in var.bucket_iam_bindings : "${binding.bucket}-${binding.role}-${binding.member}" => binding }

  bucket = each.value.bucket
  role   = each.value.role
  member = each.value.member
}
