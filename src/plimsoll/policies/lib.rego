# Shared helpers for the Plimsoll v0.1 rule pack.
#
# Everything here reads `terraform plan` JSON. Two sections of it matter and they answer
# different questions:
#
#   planned_values  what will exist, with values resolved. Use it for "is this attribute
#                   set to something dangerous".
#   configuration   what was written, with references intact. Use it for "does anything
#                   point at this bucket" and for literal values that never survive into
#                   planned_values.
#
# A value that depends on something not yet created is *unknown* at plan time and is
# absent from planned_values entirely. Rules are written so an unknown value is never
# reported as a violation: a rule that fires on "I could not tell" is a rule teams
# switch off.
package plimsoll.lib

import rego.v1

# Every planned resource, flattened across module nesting.
resources contains r if {
	walk(input.planned_values, [path, value])
	path[count(path) - 1] == "resources"
	is_array(value)
	some r in value
	is_string(r.address)
	is_string(r.type)
}

# Every resource in the configuration section, which keeps references and literals.
config_resources contains r if {
	walk(input.configuration, [path, value])
	path[count(path) - 1] == "resources"
	is_array(value)
	some r in value
	is_string(r.address)
	is_object(r.expressions)
}

# A comprehension rather than a partial set: Rego partial rules cannot take arguments.
resources_of(rtype) := {r |
	some r in resources
	r.type == rtype
}

# "aws_s3_bucket.reports.id" -> "aws_s3_bucket.reports"
base_address(ref) := addr if {
	parts := split(ref, ".")
	count(parts) >= 2
	addr := concat(".", [parts[0], parts[1]])
}

# [referrer type, bucket address] for every resource whose `bucket` argument points at a
# bucket in this configuration -- by reference, or by repeating the same literal name.
#
# This is how "the bucket has no public access block" is asked. Doing it on planned
# values would not work: `bucket = aws_s3_bucket.reports.id` is unknown at plan time, so
# the association only exists in the configuration section.
bucket_association contains [r.type, target] if {
	some r in config_resources
	some ref in r.expressions.bucket.references
	startswith(ref, "aws_s3_bucket.")
	target := base_address(ref)
}

bucket_association contains [r.type, b.address] if {
	some r in config_resources
	name := r.expressions.bucket.constant_value
	some b in config_resources
	b.type == "aws_s3_bucket"
	b.expressions.bucket.constant_value == name
}

# True when nothing of type `rtype` is attached to bucket `addr`.
bucket_lacks(rtype, addr) if {
	not [rtype, addr] in bucket_association
}

to_list(x) := [x] if is_string(x)

to_list(x) := x if is_array(x)

# IAM policy documents arrive as `jsonencode(...)` strings.
document(raw) := doc if {
	is_string(raw)
	doc := json.unmarshal(raw)
}

statements(doc) := [doc.Statement] if is_object(doc.Statement)

statements(doc) := doc.Statement if is_array(doc.Statement)

allows(st) if st.Effect == "Allow"

wildcard_action(action) if action == "*"

wildcard_action(action) if endswith(action, ":*")

policy_resource_types := {
	"aws_iam_policy",
	"aws_iam_role_policy",
	"aws_iam_user_policy",
	"aws_iam_group_policy",
}

# Actions AWS only accepts with `Resource: "*"`. Flagging these teaches reviewers that
# the resource-wildcard rule cries wolf, which is how a rule stops being read.
global_actions := {
	"sts:GetCallerIdentity",
	"s3:ListAllMyBuckets",
	"ec2:DescribeInstances",
	"ec2:DescribeRegions",
	"ec2:DescribeAvailabilityZones",
	"iam:ListRoles",
	"iam:ListUsers",
	"iam:ListAccountAliases",
	"cloudwatch:DescribeAlarms",
	"kms:ListAliases",
	"tag:GetResources",
}

# --- security group helpers -------------------------------------------------------

# A `-1` protocol means every protocol and every port; from/to are meaningless there, so
# it is classified as an all-ports rule and nothing else.
all_ports(rule) if rule.protocol == "-1"

all_ports(rule) if {
	rule.from_port == 0
	rule.to_port == 65535
}

covers(rule, port) if {
	not all_ports(rule)
	rule.from_port <= port
	rule.to_port >= port
}

# Ingress rules open to the internet, normalised across the two resource shapes AWS
# offers: the inline `ingress` block and the standalone rule resource.
world_ingress contains [r.address, r.type, rule] if {
	some r in resources_of("aws_security_group")
	some rule in r.values.ingress
	"0.0.0.0/0" in rule.cidr_blocks
}

world_ingress contains [r.address, r.type, rule] if {
	some r in resources_of("aws_vpc_security_group_ingress_rule")
	r.values.cidr_ipv4 == "0.0.0.0/0"
	rule := {
		"from_port": r.values.from_port,
		"to_port": r.values.to_port,
		"protocol": r.values.ip_protocol,
	}
}

# --- hygiene helpers ---------------------------------------------------------------

credential_attribute(name) if {
	regex.match(
		`(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)`,
		name,
	)
}

# Values that are placeholders rather than credentials. A rule that fires on
# `password = "REPLACE_ME"` in an example is noise.
placeholder(value) if {
	regex.match(`(?i)^(changeme|replace[_-]?me|todo|example|placeholder|xxx+|\$\{.*\})$`, value)
}
