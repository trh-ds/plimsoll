# S3 rules.
#
# Four of the five are absence rules: the violation is a resource the configuration does
# not contain. That is the shape most linters handle badly and the shape AI-generated
# Terraform hits most often -- a model writes the bucket it was asked for and stops,
# because nothing in "create a bucket for reports" mentions encryption.
#
# Absence is decided from the configuration section, not planned values: `bucket =
# aws_s3_bucket.reports.id` is unknown at plan time, so the association between a bucket
# and its settings only exists where the references are.
package plimsoll

import data.plimsoll.lib
import rego.v1

public_acls := {"public-read", "public-read-write"}

# s3.acl.public -- the standalone ACL resource, and the deprecated inline argument.
findings contains f if {
	some r in lib.resources
	r.type in {"aws_s3_bucket_acl", "aws_s3_bucket"}
	r.values.acl in public_acls
	f := {
		"rule_id": "PLIM_S3_001",
		"rule_class": "s3.acl.public",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": sprintf(
			"Bucket ACL is %q, which makes its objects readable by anyone on the internet.",
			[r.values.acl],
		),
		"remediation": "Set the ACL to \"private\" and grant access through a bucket policy or IAM.",
	}
}

# s3.public_access_block.missing
findings contains f if {
	some r in lib.resources_of("aws_s3_bucket")
	lib.bucket_lacks("aws_s3_bucket_public_access_block", r.address)
	f := {
		"rule_id": "PLIM_S3_002",
		"rule_class": "s3.public_access_block.missing",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": "No aws_s3_bucket_public_access_block covers this bucket, so a later ACL or policy change can expose it without tripping any control.",
		"remediation": "Add an aws_s3_bucket_public_access_block for this bucket with all four settings true.",
	}
}

# s3.encryption.missing
findings contains f if {
	some r in lib.resources_of("aws_s3_bucket")
	lib.bucket_lacks("aws_s3_bucket_server_side_encryption_configuration", r.address)
	f := {
		"rule_id": "PLIM_S3_003",
		"rule_class": "s3.encryption.missing",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": "No server-side encryption configuration is declared for this bucket.",
		"remediation": "Add an aws_s3_bucket_server_side_encryption_configuration with AES256 or aws:kms.",
	}
}

# s3.versioning.disabled -- absent, or present and not Enabled.
findings contains f if {
	some r in lib.resources_of("aws_s3_bucket")
	lib.bucket_lacks("aws_s3_bucket_versioning", r.address)
	f := {
		"rule_id": "PLIM_S3_004",
		"rule_class": "s3.versioning.disabled",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": "Versioning is not configured for this bucket, so an overwrite or delete is unrecoverable.",
		"remediation": "Add an aws_s3_bucket_versioning resource with status Enabled.",
	}
}

findings contains f if {
	some r in lib.resources_of("aws_s3_bucket_versioning")
	some config in r.values.versioning_configuration
	config.status != "Enabled"
	f := {
		"rule_id": "PLIM_S3_004",
		"rule_class": "s3.versioning.disabled",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": sprintf(
			"Bucket versioning status is %q. An overwrite or delete is unrecoverable.",
			[config.status],
		),
		"remediation": "Set versioning_configuration.status to \"Enabled\".",
	}
}

# s3.logging.missing
findings contains f if {
	some r in lib.resources_of("aws_s3_bucket")
	lib.bucket_lacks("aws_s3_bucket_logging", r.address)
	not is_log_target(r.address)
	f := {
		"rule_id": "PLIM_S3_005",
		"rule_class": "s3.logging.missing",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": "No access logging is configured for this bucket, so reads of its objects leave no trail.",
		"remediation": "Add an aws_s3_bucket_logging resource pointing at a log bucket.",
	}
}

# The bucket that *receives* access logs is exempt. Requiring it to log its own reads
# either recurses or forces a second bucket that also needs one, and the rule ends up
# unsatisfiable -- which is how a rule gets suppressed rather than fixed.
is_log_target(addr) if {
	some r in lib.config_resources
	r.type == "aws_s3_bucket_logging"
	some ref in r.expressions.target_bucket.references
	lib.base_address(ref) == addr
}
