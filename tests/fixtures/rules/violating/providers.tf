# The violating corpus: every rule in the v0.1 pack fires against this directory.
#
# One workspace, five files by rule group. Terraform merges every `.tf` in a directory
# into one configuration, so this plans as a single unit and `opa eval` sees all twenty
# rule classes at once. That is a stronger guarantee than twenty separate directories and
# far less material to keep in step.
#
# It is never applied. `terraform apply` is not invoked anywhere in this project.
#
# The credentials below are AWS's own documented example key pair. They are not valid
# anywhere and are here because `provider.static_credentials` needs something
# credential-shaped to find.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# provider.static_credentials
provider "aws" {
  region     = "us-east-1"
  access_key = "AKIAIOSFODNN7EXAMPLE"
  secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
}

# There is deliberately no aws_cloudtrail here. logging.cloudtrail.absent fires on the
# absence of one in a configuration that provisions IAM and S3.
