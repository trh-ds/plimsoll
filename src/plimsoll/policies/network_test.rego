# The property worth testing here is exclusivity, not detection.
#
# One bad ingress rule must produce one finding. If a `0.0.0.0/0` rule spanning the whole
# port range were reported as `all_ports` *and* SSH *and* RDP *and* "other", a single
# mistake would read as four problems and the reviewer would stop counting. That is the
# kind of thing a fixture corpus proves by accident and a unit test proves on purpose.
package plimsoll_network_test

import data.plimsoll
import rego.v1

sg(ingress) := {"planned_values": {"root_module": {"resources": [{
	"address": "aws_security_group.web",
	"type": "aws_security_group",
	"values": {"ingress": ingress},
}]}}}

rule(from, to) := {
	"from_port": from,
	"to_port": to,
	"protocol": "tcp",
	"cidr_blocks": ["0.0.0.0/0"],
}

private(from, to) := object.union(rule(from, to), {"cidr_blocks": ["10.0.0.0/16"]})

classes(found) := {f.rule_class | some f in found}

test_ssh_open_to_the_world if {
	found := plimsoll.findings with input as sg([rule(22, 22)])
	classes(found) == {"sg.ingress.world_ssh"}
}

test_rdp_open_to_the_world if {
	found := plimsoll.findings with input as sg([rule(3389, 3389)])
	classes(found) == {"sg.ingress.world_rdp"}
}

test_any_other_port_open_to_the_world if {
	found := plimsoll.findings with input as sg([rule(8080, 8080)])
	classes(found) == {"sg.ingress.world_open"}
}

# One rule, one finding. Not four.
test_the_full_range_is_reported_once_as_all_ports if {
	found := plimsoll.findings with input as sg([rule(0, 65535)])
	classes(found) == {"sg.ingress.all_ports"}
}

# `-1` means every protocol and every port; from/to are meaningless there.
test_protocol_minus_one_is_all_ports if {
	any_protocol := object.union(rule(0, 0), {"protocol": "-1"})
	found := plimsoll.findings with input as sg([any_protocol])
	classes(found) == {"sg.ingress.all_ports"}
}

# A range that happens to contain 22 is an SSH exposure, whatever else it contains.
test_a_range_covering_ssh_is_reported_as_ssh if {
	found := plimsoll.findings with input as sg([rule(20, 30)])
	classes(found) == {"sg.ingress.world_ssh"}
}

test_a_private_cidr_is_not_reported if {
	ingress := [private(22, 22), private(3389, 3389), private(0, 65535)]
	found := plimsoll.findings with input as sg(ingress)
	classes(found) == set()
}

# The standalone rule resource, which is the shape terraform is moving people towards.
test_the_standalone_ingress_rule_resource_is_read_too if {
	standalone := {"planned_values": {"root_module": {"resources": [{
		"address": "aws_vpc_security_group_ingress_rule.ssh",
		"type": "aws_vpc_security_group_ingress_rule",
		"values": {
			"cidr_ipv4": "0.0.0.0/0",
			"from_port": 22,
			"to_port": 22,
			"ip_protocol": "tcp",
		},
	}]}}}
	found := plimsoll.findings with input as standalone
	classes(found) == {"sg.ingress.world_ssh"}
}
