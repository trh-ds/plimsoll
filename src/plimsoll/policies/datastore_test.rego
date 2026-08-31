# `opa test src/plimsoll/policies` runs these. They exist so a rule can be developed and
# corrected without terraform in the loop: a full plan is thirty seconds and a network
# round trip, and nobody iterates on a rule at that cadence.
#
# The inputs are hand-built minimal plan documents. That is safe *here* and would not be
# safe for an adapter fixture -- the difference is that these assert what a rule does with
# a shape, while the adapter fixtures assert what shape a tool actually emits. Only the
# second can be got wrong by writing it from documentation, which is why
# `tests/fixtures/` is captured from real runs and this file is not.
package plimsoll_datastore_test

import data.plimsoll
import rego.v1

plan(resources) := {"planned_values": {"root_module": {"resources": resources}}}

classes(found) := {f.rule_class | some f in found}

volume(fields) := {
	"address": "aws_ebs_volume.scratch",
	"type": "aws_ebs_volume",
	"values": object.union({"size": 8}, fields),
}

launch_template(ebs) := {
	"address": "aws_launch_template.workers",
	"type": "aws_launch_template",
	"values": {"block_device_mappings": [{"ebs": [object.union({"volume_size": 20}, ebs)]}]},
}

database(fields) := {
	"address": "aws_db_instance.reports",
	"type": "aws_db_instance",
	"values": fields,
}

test_unencrypted_volume_is_reported if {
	found := plimsoll.findings with input as plan([volume({})])
	"ebs.encryption.missing" in classes(found)
}

test_encrypted_volume_is_not_reported if {
	found := plimsoll.findings with input as plan([volume({"encrypted": true})])
	not "ebs.encryption.missing" in classes(found)
}

test_unencrypted_launch_template_device_is_reported if {
	found := plimsoll.findings with input as plan([launch_template({})])
	"ebs.encryption.missing" in classes(found)
}

# The regression this file was written for. The AWS provider types `encrypted` as a
# string on a launch template's block device, so plan JSON carries "true", not true.
# Comparing to `true` alone reported every encrypted launch template as unencrypted.
test_a_string_true_counts_as_encrypted if {
	found := plimsoll.findings with input as plan([launch_template({"encrypted": "true"})])
	not "ebs.encryption.missing" in classes(found)
}

test_a_string_false_is_still_unencrypted if {
	found := plimsoll.findings with input as plan([launch_template({"encrypted": "false"})])
	"ebs.encryption.missing" in classes(found)
}

test_a_boolean_true_counts_as_encrypted if {
	found := plimsoll.findings with input as plan([launch_template({"encrypted": true})])
	not "ebs.encryption.missing" in classes(found)
}

test_absent_storage_encryption_is_reported if {
	found := plimsoll.findings with input as plan([database({})])
	"rds.encryption.missing" in classes(found)
}

test_explicit_false_storage_encryption_is_reported if {
	found := plimsoll.findings with input as plan([database({"storage_encrypted": false})])
	"rds.encryption.missing" in classes(found)
}

test_encrypted_database_with_backups_is_clean if {
	fields := {
		"storage_encrypted": true,
		"backup_retention_period": 7,
		"publicly_accessible": false,
	}
	found := plimsoll.findings with input as plan([database(fields)])
	classes(found) == set()
}

test_public_database_is_reported if {
	found := plimsoll.findings with input as plan([database({"publicly_accessible": true})])
	"rds.public.enabled" in classes(found)
}

test_zero_retention_is_reported if {
	found := plimsoll.findings with input as plan([database({"backup_retention_period": 0})])
	"rds.backup.disabled" in classes(found)
}
