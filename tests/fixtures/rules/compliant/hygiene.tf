# Hygiene group, fixed. No literal anywhere a credential could live, and the CloudTrail
# that `logging.cloudtrail.absent` asks for.

# secret.hardcoded -- the value comes from wherever the caller sources it.
resource "aws_ssm_parameter" "legacy_key" {
  name  = "/plimsoll/fixture/legacy"
  type  = "SecureString"
  value = var.legacy_parameter
}

resource "aws_redshift_cluster" "analytics" {
  cluster_identifier  = "plimsoll-fixture-compliant-analytics"
  database_name       = "analytics"
  master_username     = "plimsoll"
  master_password     = var.redshift_password
  node_type           = "dc2.large"
  cluster_type        = "single-node"
  encrypted           = true
  skip_final_snapshot = true
}

# logging.cloudtrail.absent -- an audit trail for the IAM and S3 resources above.
resource "aws_cloudtrail" "audit" {
  name                       = "plimsoll-fixture-compliant-audit"
  s3_bucket_name             = aws_s3_bucket.logs.id
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true
}
