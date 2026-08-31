# Data store group, fixed. Every default-unsafe attribute set explicitly.

resource "aws_db_instance" "reports" {
  identifier              = "plimsoll-fixture-compliant-reports"
  engine                  = "postgres"
  instance_class          = "db.t3.micro"
  allocated_storage       = 20
  username                = "plimsoll"
  password                = var.db_password # secret.hardcoded
  storage_encrypted       = true            # rds.encryption.missing
  publicly_accessible     = false           # rds.public.enabled
  backup_retention_period = 7               # rds.backup.disabled
  skip_final_snapshot     = true
}

# ebs.encryption.missing
resource "aws_ebs_volume" "scratch" {
  availability_zone = "us-east-1a"
  size              = 8
  encrypted         = true
}

# ebs.encryption.missing, on the block device inside a launch template.
resource "aws_launch_template" "workers" {
  name          = "plimsoll-fixture-compliant-workers"
  image_id      = "ami-0abcdef1234567890"
  instance_type = "t3.micro"

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_size = 20
      encrypted   = true
    }
  }
}

# ebs.encryption.missing, on a root block device.
resource "aws_instance" "worker" {
  ami           = "ami-0abcdef1234567890"
  instance_type = "t3.micro"

  root_block_device {
    volume_size = 20
    encrypted   = true
  }
}
