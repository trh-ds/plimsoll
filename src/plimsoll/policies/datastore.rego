# RDS and EBS rules.
#
# All four are default-unsafe attributes: leave `storage_encrypted` out and you get an
# unencrypted database, leave `encrypted` out and you get an unencrypted volume. A model
# that writes only the attributes the prompt mentioned produces exactly this, which is
# why absence is treated the same as an explicit `false`.
package plimsoll

import data.plimsoll.lib
import rego.v1

encryptable_databases := {"aws_db_instance", "aws_rds_cluster"}

# rds.encryption.missing
findings contains f if {
	some r in lib.resources
	r.type in encryptable_databases
	object.get(r.values, "storage_encrypted", false) != true
	f := {
		"rule_id": "PLIM_RDS_001",
		"rule_class": "rds.encryption.missing",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": "Storage encryption is not enabled. Encryption at rest cannot be turned on later without recreating the database from a snapshot.",
		"remediation": "Set storage_encrypted = true.",
	}
}

# rds.public.enabled
findings contains f if {
	some r in lib.resources_of("aws_db_instance")
	r.values.publicly_accessible == true
	f := {
		"rule_id": "PLIM_RDS_002",
		"rule_class": "rds.public.enabled",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": "The database is publicly accessible. It receives a public IP and is reachable from outside the VPC, leaving the security group as the only control.",
		"remediation": "Set publicly_accessible = false and reach the database from inside the VPC.",
	}
}

# rds.backup.disabled
findings contains f if {
	some r in lib.resources
	r.type in encryptable_databases
	object.get(r.values, "backup_retention_period", 0) == 0
	f := {
		"rule_id": "PLIM_RDS_003",
		"rule_class": "rds.backup.disabled",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": "Automated backups are disabled (backup_retention_period is 0). There is no point-in-time recovery.",
		"remediation": "Set backup_retention_period to at least 7.",
	}
}

# ebs.encryption.missing -- standalone volumes.
findings contains f if {
	some r in lib.resources_of("aws_ebs_volume")
	object.get(r.values, "encrypted", false) != true
	f := {
		"rule_id": "PLIM_EBS_001",
		"rule_class": "ebs.encryption.missing",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": "EBS volume is not encrypted.",
		"remediation": "Set encrypted = true.",
	}
}

# ebs.encryption.missing -- block devices declared inside an instance or launch template.
findings contains f if {
	some r in lib.resources
	some device in block_devices(r)
	object.get(device, "encrypted", false) != true
	f := {
		"rule_id": "PLIM_EBS_001",
		"rule_class": "ebs.encryption.missing",
		"resource_address": r.address,
		"resource_type": r.type,
		"message": "A block device attached here is not encrypted.",
		"remediation": "Set encrypted = true on the block device configuration.",
	}
}

block_devices(r) := devices if {
	r.type == "aws_instance"
	devices := array.concat(
		object.get(r.values, "root_block_device", []),
		object.get(r.values, "ebs_block_device", []),
	)
}

block_devices(r) := devices if {
	r.type == "aws_launch_template"
	devices := [ebs |
		some mapping in object.get(r.values, "block_device_mappings", [])
		some ebs in object.get(mapping, "ebs", [])
	]
}
