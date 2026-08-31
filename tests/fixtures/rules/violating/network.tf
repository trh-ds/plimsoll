# Network group: sg.ingress.world_ssh, sg.ingress.world_rdp, sg.ingress.all_ports,
# sg.ingress.world_open.
#
# The four classes are mutually exclusive by construction, so one rule cannot produce
# four findings. Four rules produce four findings, which is what this fixture proves.

resource "aws_vpc" "fixture" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_security_group" "open" {
  name   = "plimsoll-fixture-open"
  vpc_id = aws_vpc.fixture.id

  # sg.ingress.world_ssh
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # sg.ingress.world_rdp
  ingress {
    from_port   = 3389
    to_port     = 3389
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # sg.ingress.world_open -- anything else reachable from the internet.
  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # sg.ingress.all_ports -- reported once as all_ports, never additionally as SSH or RDP.
  ingress {
    from_port   = 0
    to_port     = 65535
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
