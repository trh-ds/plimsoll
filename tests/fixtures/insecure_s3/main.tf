# Deliberately non-compliant. This is the smoke-test fixture named in CLAUDE.md and the
# README, and the corpus the checkov fixtures were captured from.
#
# Violations here are the ones the v0.1 rule pack in specifications.md covers:
#   s3.acl.public                  public-read ACL
#   s3.encryption.missing          no server-side encryption
#   s3.versioning.disabled         versioning explicitly suspended
#   s3.logging.missing             no access logging
#   s3.public_access_block.missing no aws_s3_bucket_public_access_block
#   iam.action.wildcard            Action "*"
#   iam.resource.wildcard          Resource "*"
#
# It is never applied. `terraform apply` is not invoked anywhere in this project.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

resource "aws_s3_bucket" "reports" {
  bucket = "plimsoll-fixture-insecure-reports"
}

resource "aws_s3_bucket_acl" "reports" {
  bucket = aws_s3_bucket.reports.id
  acl    = "public-read"
}

resource "aws_s3_bucket_versioning" "reports" {
  bucket = aws_s3_bucket.reports.id

  versioning_configuration {
    status = "Suspended"
  }
}

resource "aws_iam_role" "reports_reader" {
  name = "plimsoll-fixture-reports-reader"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ec2.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "reports_access" {
  name = "plimsoll-fixture-reports-access"
  role = aws_iam_role.reports_reader.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "*"
        Resource = "*"
      }
    ]
  })
}
