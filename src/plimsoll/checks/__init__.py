"""Check adapters.

Importing this package registers every adapter with the gate's registry.
"""

from plimsoll.checks import checkov, opa, terraform_plan, terraform_validate

__all__ = ["checkov", "opa", "terraform_plan", "terraform_validate"]
