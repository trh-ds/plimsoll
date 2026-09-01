# S3 rules, without terraform in the loop. See `datastore_test.rego` for why these inputs
# are hand-built and `tests/fixtures/` is not.
#
# Four of the five rules are absence rules, and absence is decided from the
# `configuration` section rather than from planned values -- `bucket =
# aws_s3_bucket.reports.id` is unknown at plan time, so the association between a bucket
# and its settings exists only where the references are. That makes these inputs longer
# than the other files': every case has to carry both halves of the plan document, and a
# test written against planned values alone would assert nothing about the rule that
# ships.
package plimsoll_s3_test

import data.plimsoll
import rego.v1

# `logging.cloudtrail.absent` fires on any configuration holding an `aws_s3_bucket` and no
# trail, which is every input below. Subtracted so these can assert an exact set; the rule
# is tested in `hygiene_test.rego`.
classes(found) := {f.rule_class | some f in found} - {"logging.cloudtrail.absent"}

# A settings resource attached to a bucket by reference, the way terraform writes it.
attached(rtype, name, values) := {
	"planned": {
		"address": sprintf("%s.%s", [rtype, name]),
		"type": rtype,
		"values": values,
	},
	"config": {
		"address": sprintf("%s.%s", [rtype, name]),
		"type": rtype,
		"expressions": {"bucket": {"references": ["aws_s3_bucket.reports.id", "aws_s3_bucket.reports"]}},
	},
}

bucket(values) := {
	"planned": {
		"address": "aws_s3_bucket.reports",
		"type": "aws_s3_bucket",
		"values": values,
	},
	"config": {
		"address": "aws_s3_bucket.reports",
		"type": "aws_s3_bucket",
		"expressions": {"bucket": {"constant_value": "reports"}},
	},
}

# Both halves of a plan document, built from the pairs above.
plan(parts) := {
	"planned_values": {"root_module": {"resources": [p.planned | some p in parts]}},
	"configuration": {"root_module": {"resources": [p.config | some p in parts]}},
}

# Every settings resource a bucket needs to be clean, so a test can name the one it drops.
settings := [
	attached("aws_s3_bucket_public_access_block", "reports", {"block_public_acls": true}),
	attached(
		"aws_s3_bucket_server_side_encryption_configuration", "reports",
		{},
	),
	attached("aws_s3_bucket_versioning", "reports", {"versioning_configuration": [{"status": "Enabled"}]}),
	attached("aws_s3_bucket_logging", "reports", {"target_bucket": "logs"}),
]

without(rtype) := [s | some s in settings; s.planned.type != rtype]

test_a_fully_configured_bucket_is_clean if {
	found := plimsoll.findings with input as plan(array.concat([bucket({"acl": "private"})], settings))
	classes(found) == set()
}

test_a_bare_bucket_reports_all_four_absences if {
	found := plimsoll.findings with input as plan([bucket({})])
	classes(found) == {
		"s3.public_access_block.missing",
		"s3.encryption.missing",
		"s3.versioning.disabled",
		"s3.logging.missing",
	}
}

test_a_missing_public_access_block_is_reported if {
	parts := array.concat([bucket({})], without("aws_s3_bucket_public_access_block"))
	found := plimsoll.findings with input as plan(parts)
	classes(found) == {"s3.public_access_block.missing"}
}

test_a_missing_encryption_configuration_is_reported if {
	parts := array.concat([bucket({})], without("aws_s3_bucket_server_side_encryption_configuration"))
	found := plimsoll.findings with input as plan(parts)
	classes(found) == {"s3.encryption.missing"}
}

test_a_missing_versioning_resource_is_reported if {
	parts := array.concat([bucket({})], without("aws_s3_bucket_versioning"))
	found := plimsoll.findings with input as plan(parts)
	classes(found) == {"s3.versioning.disabled"}
}

test_a_missing_logging_resource_is_reported if {
	parts := array.concat([bucket({})], without("aws_s3_bucket_logging"))
	found := plimsoll.findings with input as plan(parts)
	classes(found) == {"s3.logging.missing"}
}

# Present but switched off is a violation, not an absence, and it comes from planned
# values rather than from the reference graph.
test_suspended_versioning_is_reported if {
	suspended := attached(
		"aws_s3_bucket_versioning", "reports",
		{"versioning_configuration": [{"status": "Suspended"}]},
	)
	parts := array.concat(
		array.concat([bucket({})], without("aws_s3_bucket_versioning")),
		[suspended],
	)
	found := plimsoll.findings with input as plan(parts)
	classes(found) == {"s3.versioning.disabled"}
}

test_a_public_read_acl_is_reported if {
	parts := array.concat([bucket({"acl": "public-read"})], settings)
	found := plimsoll.findings with input as plan(parts)
	classes(found) == {"s3.acl.public"}
}

test_a_public_read_write_acl_is_reported if {
	parts := array.concat([bucket({"acl": "public-read-write"})], settings)
	found := plimsoll.findings with input as plan(parts)
	classes(found) == {"s3.acl.public"}
}

test_a_private_acl_is_not_reported if {
	parts := array.concat([bucket({"acl": "private"})], settings)
	found := plimsoll.findings with input as plan(parts)
	classes(found) == set()
}

# The standalone ACL resource is the shape terraform is moving people towards, and it
# carries `acl` on itself rather than on the bucket.
test_the_standalone_acl_resource_is_read_too if {
	acl := attached("aws_s3_bucket_acl", "reports", {"acl": "public-read"})
	parts := array.concat(array.concat([bucket({})], settings), [acl])
	found := plimsoll.findings with input as plan(parts)
	classes(found) == {"s3.acl.public"}
}

# A bucket associated by repeating the same literal name, rather than by reference. The
# rule has to see both or half of real configurations report false absences.
test_association_by_a_repeated_literal_name_counts if {
	by_name := {
		"planned": {
			"address": "aws_s3_bucket_versioning.reports",
			"type": "aws_s3_bucket_versioning",
			"values": {"versioning_configuration": [{"status": "Enabled"}]},
		},
		"config": {
			"address": "aws_s3_bucket_versioning.reports",
			"type": "aws_s3_bucket_versioning",
			"expressions": {"bucket": {"constant_value": "reports"}},
		},
	}
	parts := array.concat(
		array.concat([bucket({})], without("aws_s3_bucket_versioning")),
		[by_name],
	)
	found := plimsoll.findings with input as plan(parts)
	classes(found) == set()
}

# The regression that makes the logging rule satisfiable. The bucket receiving access
# logs is exempt from needing its own -- otherwise fixing the finding creates another
# one, forever, and the rule gets suppressed rather than fixed.
test_the_log_target_bucket_does_not_need_its_own_logging if {
	logs := {
		"planned": {"address": "aws_s3_bucket.logs", "type": "aws_s3_bucket", "values": {}},
		"config": {
			"address": "aws_s3_bucket.logs",
			"type": "aws_s3_bucket",
			"expressions": {"bucket": {"constant_value": "logs"}},
		},
	}
	logging := {
		"planned": {
			"address": "aws_s3_bucket_logging.reports",
			"type": "aws_s3_bucket_logging",
			"values": {},
		},
		"config": {
			"address": "aws_s3_bucket_logging.reports",
			"type": "aws_s3_bucket_logging",
			"expressions": {
				"bucket": {"references": ["aws_s3_bucket.reports.id", "aws_s3_bucket.reports"]},
				"target_bucket": {"references": ["aws_s3_bucket.logs.id", "aws_s3_bucket.logs"]},
			},
		},
	}
	parts := array.concat(
		array.concat([bucket({}), logs], without("aws_s3_bucket_logging")),
		[logging],
	)
	found := plimsoll.findings with input as plan(parts)
	not "s3.logging.missing" in {f.rule_class | some f in found; f.resource_address == "aws_s3_bucket.logs"}
}
