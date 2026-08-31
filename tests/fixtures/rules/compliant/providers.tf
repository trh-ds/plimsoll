# The compliant corpus: the same resources as `../violating`, every violation fixed.
#
# This is the harder half of the pair and the more useful one. A rule that fires is easy
# to write; a rule that fires *only* when it should is the thing a team keeps switched
# on. Every rule class in the pack must be absent from a scan of this directory.
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

# provider.static_credentials: no credential arguments. The provider reads the
# environment, a shared config profile, or an instance role.
provider "aws" {
  region = "us-east-1"
}

# In a real configuration these are sourced from a secrets manager. They carry defaults
# here only so the fixture plans without a `.tfvars` file, and the defaults satisfy the
# providers' own password-format validation, which runs at plan time.
#
# A default on a `variable` block is not what `secret.hardcoded` looks at: that rule
# walks resource expressions, and a resource whose argument is `var.x` carries a
# reference, not a `constant_value`. Referencing a variable is the fix, and this proves
# the fix works rather than merely moving the literal somewhere the rule cannot see --
# the same file in `../violating` sets the literal on the resource itself.

variable "db_password" {
  description = "Sourced from a secrets manager by the caller, never committed."
  type        = string
  sensitive   = true
  default     = "FixtureOnly1"
}

variable "redshift_password" {
  description = "Sourced from a secrets manager by the caller, never committed."
  type        = string
  sensitive   = true
  default     = "FixtureOnly1"
}

variable "legacy_parameter" {
  description = "Whatever the caller stores in the legacy SSM parameter."
  type        = string
  sensitive   = true
  default     = "FixtureOnly1"
}
