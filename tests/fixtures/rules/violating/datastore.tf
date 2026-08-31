# Data store group: rds.encryption.missing, rds.public.enabled, rds.backup.disabled,
# ebs.encryption.missing.
#
# All four are default-unsafe attributes. A model that writes only what the prompt
# mentioned produces exactly this, which is why absence is treated as an explicit false.

# rds.encryption.missing (absent), rds.public.enabled, rds.backup.disabled,
# and secret.hardcoded from the literal password.
resource "aws_db_instance" "reports" {
  identifier          = "plimsoll-fixture-reports"
  engine              = "postgres"
  instance_class      = "db.t3.micro"
  allocated_storage   = 20
  username            = "plimsoll"
  password            = "correcthorsebattery"
  publicly_accessible = true
  skip_final_snapshot = true
}

# ebs.encryption.missing -- a standalone volume.
resource "aws_ebs_volume" "scratch" {
  availability_zone = "us-east-1a"
  size              = 8
}

# ebs.encryption.missing -- and again from a block device inside a launch template,
# which is the shape a standalone-volume check alone would miss.
resource "aws_launch_template" "workers" {
  name          = "plimsoll-fixture-workers"
  image_id      = "ami-0abcdef1234567890"
  instance_type = "t3.micro"

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_size = 20
    }
  }
}

# ebs.encryption.missing -- and once more from a root block device on an instance, the
# other branch of the same rule.
resource "aws_instance" "worker" {
  ami           = "ami-0abcdef1234567890"
  instance_type = "t3.micro"

  root_block_device {
    volume_size = 20
  }
}
