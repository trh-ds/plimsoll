"""Check adapters.

Importing this package registers every adapter with the gate's registry.
"""

from plimsoll.checks import terraform_validate

__all__ = ["terraform_validate"]
