"""Check adapters.

Importing this package registers every adapter with the gate's registry.
"""

from plimsoll.checks import checkov, terraform_validate

__all__ = ["checkov", "terraform_validate"]
