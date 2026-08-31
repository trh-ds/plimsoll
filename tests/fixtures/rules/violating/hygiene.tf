# Hygiene group: secret.hardcoded.
#
# `provider.static_credentials` is in providers.tf, where the provider block is, and
# `logging.cloudtrail.absent` fires on the absence of a CloudTrail across the whole
# directory. Both are listed here so the group is accounted for in one place.
#
# Nothing in this file is a live credential. The AWS key is the vendor's own documented
# example; the password is a passphrase from a webcomic.

# secret.hardcoded -- an AWS access key id recognisable on sight, whatever it is called.
resource "aws_ssm_parameter" "legacy_key" {
  name  = "/plimsoll/fixture/legacy"
  type  = "String"
  value = "AKIAIOSFODNN7EXAMPLE"
}

# secret.hardcoded -- a credential-shaped attribute with a literal value. The password on
# aws_db_instance.reports in datastore.tf is caught the same way.
resource "aws_redshift_cluster" "analytics" {
  cluster_identifier  = "plimsoll-fixture-analytics"
  database_name       = "analytics"
  master_username     = "plimsoll"
  master_password     = "Tr0ub4dor3Horse"
  node_type           = "dc2.large"
  cluster_type        = "single-node"
  skip_final_snapshot = true
}
