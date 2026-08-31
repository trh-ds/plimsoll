# Network group, fixed. The same four ingress rules, none of them open to the internet.

resource "aws_vpc" "fixture" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_security_group" "open" {
  name   = "plimsoll-fixture-scoped"
  vpc_id = aws_vpc.fixture.id

  # sg.ingress.world_ssh -- management network only.
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }

  # sg.ingress.world_rdp
  ingress {
    from_port   = 3389
    to_port     = 3389
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }

  # sg.ingress.world_open
  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }

  # sg.ingress.all_ports -- the full range is fine inside the VPC; the rule is about
  # 0.0.0.0/0, not about breadth on its own.
  ingress {
    from_port   = 0
    to_port     = 65535
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }
}
