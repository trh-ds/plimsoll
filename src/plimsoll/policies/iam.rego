# IAM rules. Over-broad permissions are the most-reported failure mode in AI-generated
# Terraform: a model asked for "a role that can read the reports bucket" reaches for
# `Action: "*"` because it satisfies the request and nothing in the prompt punishes it.
#
# Severity for every rule_class below lives in `checks/severity_map.py`. It is not in
# this file, and not inferred from the wording, because a severity a rule author can
# raise on their own is a severity nobody reviews.
package plimsoll

import data.plimsoll.lib
import rego.v1

# iam.action.wildcard -- `Action: "*"` or `"service:*"` in an Allow statement.
findings contains f if {
	some r in lib.resources
	r.type in lib.policy_resource_types
	doc := lib.document(r.values.policy)
	some st in lib.statements(doc)
	lib.allows(st)
	some action in lib.to_list(st.Action)
	lib.wildcard_action(action)
	f := {
		"rule_id": "PLIM_IAM_001",
		"rule_class": "iam.action.wildcard",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": sprintf(
			"Policy allows the wildcard action %q. Anything holding this policy can perform every operation the wildcard covers, not the ones the application needs.",
			[action],
		),
		"remediation": "Replace the wildcard with the specific actions this principal calls.",
	}
}

# iam.resource.wildcard -- `Resource: "*"` on actions that accept a narrower scope.
findings contains f if {
	some r in lib.resources
	r.type in lib.policy_resource_types
	doc := lib.document(r.values.policy)
	some st in lib.statements(doc)
	lib.allows(st)
	"*" in lib.to_list(st.Resource)
	some action in lib.to_list(st.Action)
	not action in lib.global_actions
	f := {
		"rule_id": "PLIM_IAM_002",
		"rule_class": "iam.resource.wildcard",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": sprintf(
			"Policy allows %q against every resource in the account. %s can be scoped to an ARN.",
			[action, action],
		),
		"remediation": "Replace \"*\" with the ARNs this policy is meant to cover.",
	}
}

# iam.trust.wildcard_principal -- anyone on the internet may assume this role.
findings contains f if {
	some r in lib.resources_of("aws_iam_role")
	doc := lib.document(r.values.assume_role_policy)
	some st in lib.statements(doc)
	lib.allows(st)
	principal_is_wildcard(st.Principal)
	f := {
		"rule_id": "PLIM_IAM_003",
		"rule_class": "iam.trust.wildcard_principal",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": "Trust policy allows any principal to assume this role. Every permission the role holds is reachable by any AWS account.",
		"remediation": "Name the account, service, or federated principal that should assume this role.",
	}
}

principal_is_wildcard(principal) if principal == "*"

principal_is_wildcard(principal) if "*" in lib.to_list(principal.AWS)

# iam.policy.admin_attached -- the AWS-managed AdministratorAccess policy.
findings contains f if {
	some r in lib.resources
	endswith(r.values.policy_arn, "/AdministratorAccess")
	f := {
		"rule_id": "PLIM_IAM_004",
		"rule_class": "iam.policy.admin_attached",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": "AdministratorAccess is attached here. This grants full control of the account, including the ability to remove the controls that would detect its misuse.",
		"remediation": "Attach a policy scoped to the operations this identity performs.",
	}
}
