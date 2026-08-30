"""Terraform resource address parsing, shared by the adapters.

Every tool reports addresses in the same syntax, so the splitting lives here rather than
in each adapter. `resource_type` is half of the dedup key, so two adapters deriving it
differently would silently stop deduplicating.
"""

from __future__ import annotations


def split_address(address: str) -> tuple[str, str]:
    """Return (address, resource_type) for a terraform address.

    Handles the three shapes tools emit:

        aws_s3_bucket.reports                  -> aws_s3_bucket
        data.aws_ami.latest                    -> aws_ami
        module.reports.aws_s3_bucket.data      -> aws_s3_bucket

    Returns an empty type for anything that is not an address, rather than guessing.
    """
    parts = address.split(".")
    # A module path is any number of `module.<name>` pairs before the real address.
    while len(parts) >= 2 and parts[0] == "module":
        parts = parts[2:]
    if parts and parts[0] == "data" and len(parts) >= 3:
        return address, parts[1]
    if len(parts) >= 2:
        return address, parts[0]
    return address, ""
