# IAM rules, without terraform in the loop. See `datastore_test.rego` for why these
# inputs are hand-built and `tests/fixtures/` is not.
#
# Two properties matter more than detection here. The first is that the policy document
# is a `jsonencode(...)` *string* in plan output, so every rule below goes through
# `lib.document` -- a rule that reached into `st.Action` directly would pass a test
# written against an object and find nothing in production. The second is
# `lib.global_actions`: `sts:GetCallerIdentity` cannot be scoped to an ARN, and a
# resource-wildcard rule that reports it teaches reviewers the rule cries wolf.
package plimsoll_iam_test

import data.plimsoll
import rego.v1

# `logging.cloudtrail.absent` is an account-level rule: it fires on any configuration
# holding an `aws_iam_*` resource and no trail, which is every input in this file. It is
# subtracted so these tests can assert an exact set and mean it. The rule itself is
# tested where it belongs, in `hygiene_test.rego`.
classes(found) := {f.rule_class | some f in found} - {"logging.cloudtrail.absent"}

# `policy` is a string in plan JSON because it is written as `jsonencode({...})`.
policy(statement) := {"planned_values": {"root_module": {"resources": [{
	"address": "aws_iam_policy.app",
	"type": "aws_iam_policy",
	"values": {"policy": json.marshal({"Version": "2012-10-17", "Statement": statement})},
}]}}}

role(statement) := {"planned_values": {"root_module": {"resources": [{
	"address": "aws_iam_role.app",
	"type": "aws_iam_role",
	"values": {"assume_role_policy": json.marshal({
		"Version": "2012-10-17",
		"Statement": statement,
	})},
}]}}}

attachment(arn) := {"planned_values": {"root_module": {"resources": [{
	"address": "aws_iam_role_policy_attachment.app",
	"type": "aws_iam_role_policy_attachment",
	"values": {"policy_arn": arn},
}]}}}

allow(action, resource) := {
	"Effect": "Allow",
	"Action": action,
	"Resource": resource,
}

test_a_bare_wildcard_action_is_reported if {
	found := plimsoll.findings with input as policy([allow("*", "arn:aws:s3:::reports/*")])
	classes(found) == {"iam.action.wildcard"}
}

test_a_service_wildcard_action_is_reported if {
	found := plimsoll.findings with input as policy([allow("s3:*", "arn:aws:s3:::reports/*")])
	classes(found) == {"iam.action.wildcard"}
}

test_a_named_action_on_a_named_arn_is_clean if {
	found := plimsoll.findings with input as policy([allow("s3:GetObject", "arn:aws:s3:::reports/*")])
	classes(found) == set()
}

# Effect: Deny with a wildcard is how a guardrail is written, not how one is broken.
test_a_deny_statement_is_not_reported if {
	deny := {"Effect": "Deny", "Action": "*", "Resource": "*"}
	found := plimsoll.findings with input as policy([deny])
	classes(found) == set()
}

test_a_resource_wildcard_is_reported if {
	found := plimsoll.findings with input as policy([allow("s3:GetObject", "*")])
	classes(found) == {"iam.resource.wildcard"}
}

# The false positive the exemption list exists to prevent. AWS accepts this action only
# with `Resource: "*"`, so reporting it asks for a change that cannot be made.
test_an_action_that_cannot_be_scoped_is_not_reported if {
	found := plimsoll.findings with input as policy([allow("sts:GetCallerIdentity", "*")])
	classes(found) == set()
}

# A statement mixing both still reports the wildcard resource, on the strength of the
# action that *can* be scoped.
test_a_scopable_action_alongside_a_global_one_is_still_reported if {
	statement := allow(["sts:GetCallerIdentity", "s3:GetObject"], "*")
	found := plimsoll.findings with input as policy([statement])
	"iam.resource.wildcard" in classes(found)
}

test_both_wildcards_at_once_report_both_classes if {
	found := plimsoll.findings with input as policy([allow("*", "*")])
	classes(found) == {"iam.action.wildcard", "iam.resource.wildcard"}
}

# A single-statement document is an object, not an array. `lib.statements` normalises it,
# and a rule that assumed an array would silently pass every policy written this way.
test_a_single_statement_object_is_read_like_a_list if {
	found := plimsoll.findings with input as policy(allow("*", "*"))
	classes(found) == {"iam.action.wildcard", "iam.resource.wildcard"}
}

test_a_string_principal_wildcard_is_reported if {
	trust := {"Effect": "Allow", "Principal": "*", "Action": "sts:AssumeRole"}
	found := plimsoll.findings with input as role([trust])
	classes(found) == {"iam.trust.wildcard_principal"}
}

test_an_aws_principal_wildcard_is_reported if {
	trust := {"Effect": "Allow", "Principal": {"AWS": "*"}, "Action": "sts:AssumeRole"}
	found := plimsoll.findings with input as role([trust])
	classes(found) == {"iam.trust.wildcard_principal"}
}

test_a_named_service_principal_is_clean if {
	trust := {
		"Effect": "Allow",
		"Principal": {"Service": "ec2.amazonaws.com"},
		"Action": "sts:AssumeRole",
	}
	found := plimsoll.findings with input as role([trust])
	classes(found) == set()
}

test_administrator_access_is_reported_wherever_it_is_attached if {
	found := plimsoll.findings with input as attachment("arn:aws:iam::aws:policy/AdministratorAccess")
	classes(found) == {"iam.policy.admin_attached"}
}

# `endswith` on the whole ARN, so a customer-managed policy that merely mentions the word
# is not mistaken for the AWS-managed one.
test_a_policy_named_after_it_is_not_reported if {
	found := plimsoll.findings with input as attachment("arn:aws:iam::123456789012:policy/AdministratorAccessAudit")
	classes(found) == set()
}
