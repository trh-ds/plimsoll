# Secrets and account hygiene, without terraform in the loop. See `datastore_test.rego`
# for why these inputs are hand-built and `tests/fixtures/` is not.
#
# These rules read the `configuration` section, not planned values, because that is the
# only place a literal survives: a hardcoded password is frequently marked sensitive and
# therefore redacted in planned values. A test written against planned values would pass
# while the shipped rule found nothing.
#
# The other property under test is what a finding *carries*. `Finding.raw` is redacted at
# construction, but a rule that put the matched credential in its own message would be
# redacted only by luck, so the assertion is made here on purpose.
package plimsoll_hygiene_test

import data.plimsoll
import rego.v1

classes(found) := {f.rule_class | some f in found}

config(resources) := {"configuration": {"root_module": {"resources": resources}}}

resource(rtype, expressions) := {
	"address": sprintf("%s.app", [rtype]),
	"type": rtype,
	"expressions": expressions,
}

literal(value) := {"constant_value": value}

db(attribute, value) := config([resource("aws_db_instance", {attribute: literal(value)})])

provider(expressions) := {"configuration": {"provider_config": {"aws": object.union(
	{"name": "aws"},
	{"expressions": expressions},
)}}}

test_a_literal_password_is_reported if {
	found := plimsoll.findings with input as db("password", "hunter2-not-a-real-one")
	classes(found) == {"secret.hardcoded"}
}

test_the_matched_value_never_appears_in_the_finding if {
	secret := "hunter2-not-a-real-one"
	found := plimsoll.findings with input as db("password", secret)
	every f in found {
		not contains(f.message, secret)
	}
}

test_a_variable_reference_is_not_a_literal if {
	# `password = var.db_password` has references and no constant_value.
	expressions := {"password": {"references": ["var.db_password"]}}
	found := plimsoll.findings with input as config([resource("aws_db_instance", expressions)])
	classes(found) == set()
}

# A rule that fires on `password = "CHANGEME"` in an example is noise, and noise is how a
# scanner gets switched off.
test_a_placeholder_is_not_reported if {
	found := plimsoll.findings with input as db("password", "REPLACE_ME")
	classes(found) == set()
}

test_an_interpolation_is_not_reported if {
	found := plimsoll.findings with input as db("password", "${var.db_password}")
	classes(found) == set()
}

# Short values are almost always enum members rather than credentials.
test_a_short_value_is_not_reported if {
	found := plimsoll.findings with input as db("password", "abc")
	classes(found) == set()
}

test_the_attribute_name_is_matched_case_insensitively if {
	found := plimsoll.findings with input as db("API_KEY", "sk-live-0123456789abcdef")
	classes(found) == {"secret.hardcoded"}
}

test_an_unrelated_attribute_is_not_reported if {
	found := plimsoll.findings with input as db("engine_version", "15.4-with-a-long-tail")
	classes(found) == set()
}

# The second secret rule: an AWS key id is recognisable on sight, so the attribute it
# hides behind does not matter.
test_an_aws_key_id_is_reported_under_any_attribute_name if {
	found := plimsoll.findings with input as db("description", "AKIAIOSFODNN7EXAMPLE")
	classes(found) == {"secret.hardcoded"}
}

test_a_temporary_key_id_is_reported_too if {
	found := plimsoll.findings with input as db("description", "ASIAIOSFODNN7EXAMPLE")
	classes(found) == {"secret.hardcoded"}
}

test_static_provider_credentials_are_reported if {
	expressions := {
		"access_key": literal("AKIAIOSFODNN7EXAMPLE"),
		"secret_key": literal("wJalrXUtnFEMI-K7MDENG-bPxRfiCYEXAMPLEKEY"),
	}
	found := plimsoll.findings with input as provider(expressions)
	classes(found) == {"provider.static_credentials"}
}

test_a_provider_reading_the_environment_is_clean if {
	found := plimsoll.findings with input as provider({"region": literal("eu-west-1")})
	classes(found) == set()
}

# The account-level rule the IAM and S3 test files subtract, asserted here instead.
test_iam_resources_without_a_trail_are_reported if {
	plan := {"planned_values": {"root_module": {"resources": [{
		"address": "aws_iam_role.app",
		"type": "aws_iam_role",
		"values": {},
	}]}}}
	found := plimsoll.findings with input as plan
	"logging.cloudtrail.absent" in classes(found)
}

test_a_configuration_with_a_trail_is_not_reported if {
	plan := {"planned_values": {"root_module": {"resources": [
		{"address": "aws_iam_role.app", "type": "aws_iam_role", "values": {}},
		{"address": "aws_cloudtrail.audit", "type": "aws_cloudtrail", "values": {}},
	]}}}
	found := plimsoll.findings with input as plan
	not "logging.cloudtrail.absent" in classes(found)
}

# Nothing auditable, nothing to audit. A configuration of security groups alone must not
# be told to add a trail it has no reason for.
test_a_configuration_with_nothing_auditable_is_not_reported if {
	plan := {"planned_values": {"root_module": {"resources": [{
		"address": "aws_security_group.web",
		"type": "aws_security_group",
		"values": {"ingress": []},
	}]}}}
	found := plimsoll.findings with input as plan
	not "logging.cloudtrail.absent" in classes(found)
}
