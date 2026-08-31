# Secrets and account hygiene.
#
# These read the *configuration* section rather than planned values, because that is
# where a literal survives: a hardcoded password is a `constant_value` in the
# configuration and is frequently marked sensitive -- and therefore redacted -- in
# planned values. The rule that finds credentials has to look where credentials are.
#
# Note what these rules send onward: nothing. A finding carries the attribute name and
# the resource, never the matched value. `Finding.raw` is redacted at construction, but
# a rule that put a credential in its own message would be redacted only by luck.
package plimsoll

import data.plimsoll.lib
import rego.v1

# secret.hardcoded -- a credential-shaped attribute with a literal value.
findings contains f if {
	some r in lib.config_resources
	walk(r.expressions, [path, value])
	is_object(value)
	is_string(value.constant_value)
	attribute := path[count(path) - 1]
	lib.credential_attribute(attribute)
	count(value.constant_value) >= 8
	not lib.placeholder(value.constant_value)
	f := {
		"rule_id": "PLIM_SEC_001",
		"rule_class": "secret.hardcoded",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": sprintf(
			"%s is set to a literal value in the configuration. Anything committed here is in the repository history permanently, and in every clone of it.",
			[attribute],
		),
		"remediation": "Move the value into a variable sourced from a secrets manager, or reference aws_secretsmanager_secret_version.",
	}
}

# secret.hardcoded -- a provider-issued key recognisable on sight, whatever it is called.
findings contains f if {
	some r in lib.config_resources
	walk(r.expressions, [_, value])
	is_object(value)
	is_string(value.constant_value)
	regex.match(`(AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}`, value.constant_value)
	f := {
		"rule_id": "PLIM_SEC_002",
		"rule_class": "secret.hardcoded",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": "An AWS access key id appears as a literal in this resource.",
		"remediation": "Remove the key, rotate it, and source credentials from the environment or an instance role.",
	}
}

# provider.static_credentials -- keys in the provider block itself.
findings contains f if {
	some name, provider in input.configuration.provider_config
	some attribute in {"access_key", "secret_key", "token"}
	is_string(provider.expressions[attribute].constant_value)
	f := {
		"rule_id": "PLIM_SEC_003",
		"rule_class": "provider.static_credentials",
		"resource_address": sprintf("provider.%s", [provider_label(name, provider)]),
		"resource_type": "provider",
		"message": sprintf(
			"The provider block sets %s to a literal. Every run of this configuration, by anyone who can clone it, authenticates as whoever owns that key.",
			[attribute],
		),
		"remediation": "Delete the credential arguments and let the provider read the environment, a shared config profile, or an instance role.",
	}
}

provider_label(_, provider) := label if label := provider.name

provider_label(name, provider) := name if not provider.name

# logging.cloudtrail.absent -- no audit trail in a configuration that provisions the
# things an audit trail exists to record.
findings contains f if {
	count(lib.resources_of("aws_cloudtrail")) == 0
	count(auditable) > 0
	f := {
		"rule_id": "PLIM_LOG_001",
		"rule_class": "logging.cloudtrail.absent",
		"resource_address": "",
		"resource_type": "",
		"message": sprintf(
			"This configuration provisions %d IAM or S3 resource(s) and no CloudTrail. Without one there is no record of who used them.",
			[count(auditable)],
		),
		"remediation": "Add an aws_cloudtrail with a log bucket, or confirm the account already has an organisation trail covering it.",
	}
}

auditable contains r.address if {
	some r in lib.resources
	audited_type(r.type)
}

audited_type(rtype) if startswith(rtype, "aws_iam_")

audited_type(rtype) if startswith(rtype, "aws_s3_bucket")
