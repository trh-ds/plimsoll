"""Tool rule IDs mapped to internal rule classes and severities.

Checkov's community edition emits `"severity": null` on every finding -- severity is a
Bridgecrew platform feature. So severity for checkov cannot come from the tool. It comes
from this table, which is explicit and never inferred from the check's wording.

The same table carries `rule_class`, because a class and a severity are two halves of one
decision: a check that means "this bucket is public" is `s3.acl.public` and it is
CRITICAL. Splitting them across two files keyed by the same ID would let them drift.

**Only entries verified against real checkov output belong here.** An ID typed from
memory maps a finding to the wrong class, which is invisible in the report -- the finding
still appears, just filed under something it is not. An ID that is simply absent is
visible: the finding shows up unmapped, at the default severity. Under-classification is
recoverable; misclassification is not. Every entry below was read off a real run against
`tests/fixtures/`; the rest of checkov's catalogue is added as fixtures grow to cover it.
"""

from __future__ import annotations

from typing import NamedTuple

from plimsoll.core.models import Severity


class RuleMapping(NamedTuple):
    rule_class: str
    severity: Severity


CHECKOV: dict[str, RuleMapping] = {
    # S3 -- verified against tests/fixtures/checkov/insecure_s3.json
    "CKV_AWS_20": RuleMapping("s3.acl.public", Severity.CRITICAL),
    "CKV2_AWS_6": RuleMapping("s3.public_access_block.missing", Severity.HIGH),
    "CKV_AWS_145": RuleMapping("s3.encryption.missing", Severity.HIGH),
    "CKV_AWS_21": RuleMapping("s3.versioning.disabled", Severity.MEDIUM),
    "CKV_AWS_18": RuleMapping("s3.logging.missing", Severity.MEDIUM),
    # IAM -- verified against the same fixture
    "CKV_AWS_63": RuleMapping("iam.action.wildcard", Severity.HIGH),
    "CKV_AWS_355": RuleMapping("iam.resource.wildcard", Severity.HIGH),
    "CKV_AWS_62": RuleMapping("iam.policy.admin_attached", Severity.HIGH),
    "CKV2_AWS_40": RuleMapping("iam.policy.admin_attached", Severity.HIGH),
    # Secrets -- verified against tests/fixtures/checkov/multi_framework.json
    "CKV_SECRET_2": RuleMapping("secret.hardcoded", Severity.CRITICAL),
}

UNMAPPED_RULE_CLASS_PREFIX = "checkov"


def checkov_mapping(check_id: str) -> RuleMapping:
    """Map a checkov check ID, falling back to an unclassified rule class.

    The fallback severity is deliberately below the default `--fail-on` threshold.
    Checkov ships opinions well beyond this project's scope -- it wants a lifecycle
    configuration and cross-region replication on every bucket -- and blocking a merge on
    a rule nobody has classified is how policy-as-code gets switched off. The finding is
    still reported; it just does not fail the build on its own.
    """
    mapped = CHECKOV.get(check_id)
    if mapped is not None:
        return mapped
    return RuleMapping(f"{UNMAPPED_RULE_CLASS_PREFIX}.{check_id.lower()}", Severity.MEDIUM)
