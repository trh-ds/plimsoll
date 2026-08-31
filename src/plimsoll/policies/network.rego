# Security group rules.
#
# The four classes are mutually exclusive by construction: an ingress rule spanning the
# whole port range is reported once as `all_ports`, not additionally as SSH and RDP and
# "other". One violation should produce one line, or a single bad rule reads as four
# problems and the reviewer stops counting.
package plimsoll

import data.plimsoll.lib
import rego.v1

# sg.ingress.world_ssh
findings contains f if {
	some [address, rtype, rule] in lib.world_ingress
	lib.covers(rule, 22)
	f := {
		"rule_id": "PLIM_SG_001",
		"rule_class": "sg.ingress.world_ssh",
		"resource_address": address,
		"resource_type": rtype,
		"message": "SSH (port 22) is open to 0.0.0.0/0. This is the single most scanned port on the internet; exposure is measured in minutes, not days.",
		"remediation": "Restrict the CIDR to your management network, or reach the host through SSM Session Manager instead.",
	}
}

# sg.ingress.world_rdp
findings contains f if {
	some [address, rtype, rule] in lib.world_ingress
	lib.covers(rule, 3389)
	f := {
		"rule_id": "PLIM_SG_002",
		"rule_class": "sg.ingress.world_rdp",
		"resource_address": address,
		"resource_type": rtype,
		"message": "RDP (port 3389) is open to 0.0.0.0/0. Exposed RDP is the entry point in a large share of documented ransomware incidents.",
		"remediation": "Restrict the CIDR to your management network, or use a bastion.",
	}
}

# sg.ingress.all_ports
findings contains f if {
	some [address, rtype, rule] in lib.world_ingress
	lib.all_ports(rule)
	f := {
		"rule_id": "PLIM_SG_003",
		"rule_class": "sg.ingress.all_ports",
		"resource_address": address,
		"resource_type": rtype,
		"message": "An ingress rule opens the full port range to 0.0.0.0/0. Every service on the host is reachable, including ones nobody meant to expose.",
		"remediation": "Open only the ports the service listens on.",
	}
}

# sg.ingress.world_open -- everything else open to the internet.
findings contains f if {
	some [address, rtype, rule] in lib.world_ingress
	not lib.all_ports(rule)
	not lib.covers(rule, 22)
	not lib.covers(rule, 3389)
	f := {
		"rule_id": "PLIM_SG_004",
		"rule_class": "sg.ingress.world_open",
		"resource_address": address,
		"resource_type": rtype,
		"message": sprintf(
			"Ports %v-%v are open to 0.0.0.0/0.",
			[rule.from_port, rule.to_port],
		),
		"remediation": "Restrict the CIDR, or put the service behind a load balancer or WAF if it is meant to be public.",
	}
}
