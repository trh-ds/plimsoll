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
    # Verified against tests/fixtures/checkov/rules_violating.json, each on the same
    # resource address the Rego pack reports the same class on, so the two deduplicate.
    "CKV_AWS_60": RuleMapping("iam.trust.wildcard_principal", Severity.CRITICAL),
    "CKV_AWS_274": RuleMapping("iam.policy.admin_attached", Severity.HIGH),
    "CKV_AWS_24": RuleMapping("sg.ingress.world_ssh", Severity.CRITICAL),
    "CKV_AWS_25": RuleMapping("sg.ingress.world_rdp", Severity.CRITICAL),
    "CKV_AWS_260": RuleMapping("sg.ingress.world_open", Severity.HIGH),
    "CKV_AWS_16": RuleMapping("rds.encryption.missing", Severity.HIGH),
    "CKV_AWS_17": RuleMapping("rds.public.enabled", Severity.CRITICAL),
    "CKV_AWS_3": RuleMapping("ebs.encryption.missing", Severity.HIGH),
    "CKV_AWS_41": RuleMapping("provider.static_credentials", Severity.CRITICAL),
}


class PolicyRule(NamedTuple):
    severity: Severity
    benchmark_refs: tuple[str, ...]


# The bundled Rego pack. The `.rego` files carry the logic, the message, and the
# remediation; severity and benchmark mapping live here, in Python, for the same reason
# checkov's do: a severity a rule author can raise inside their own rule file is a
# severity nobody reviews. `specifications.md` is the source for this table.
#
# Benchmark references are best-effort v0.1 mappings to CIS AWS Foundations v3.0. They
# are recorded so a compliance reviewer can find the clause, not asserted as certified
# coverage -- this project has not been through a CIS certification and does not claim to
# have been.
PLIMSOLL: dict[str, PolicyRule] = {
    "iam.action.wildcard": PolicyRule(Severity.HIGH, ("CIS-AWS-1.16",)),
    "iam.resource.wildcard": PolicyRule(Severity.HIGH, ("CIS-AWS-1.16",)),
    "iam.trust.wildcard_principal": PolicyRule(Severity.CRITICAL, ("CIS-AWS-1.16",)),
    "iam.policy.admin_attached": PolicyRule(Severity.HIGH, ("CIS-AWS-1.16",)),
    "s3.acl.public": PolicyRule(Severity.CRITICAL, ("CIS-AWS-2.1.4",)),
    "s3.public_access_block.missing": PolicyRule(Severity.HIGH, ("CIS-AWS-2.1.4",)),
    "s3.encryption.missing": PolicyRule(Severity.HIGH, ("CIS-AWS-2.1.1",)),
    "s3.versioning.disabled": PolicyRule(Severity.MEDIUM, ()),
    "s3.logging.missing": PolicyRule(Severity.MEDIUM, ("CIS-AWS-3.6",)),
    "sg.ingress.world_ssh": PolicyRule(Severity.CRITICAL, ("CIS-AWS-5.2",)),
    "sg.ingress.world_rdp": PolicyRule(Severity.CRITICAL, ("CIS-AWS-5.2",)),
    "sg.ingress.world_open": PolicyRule(Severity.HIGH, ("CIS-AWS-5.2",)),
    "sg.ingress.all_ports": PolicyRule(Severity.HIGH, ("CIS-AWS-5.2",)),
    "rds.encryption.missing": PolicyRule(Severity.HIGH, ("CIS-AWS-2.3.1",)),
    "rds.public.enabled": PolicyRule(Severity.CRITICAL, ("CIS-AWS-2.3.3",)),
    "rds.backup.disabled": PolicyRule(Severity.MEDIUM, ()),
    "ebs.encryption.missing": PolicyRule(Severity.HIGH, ("CIS-AWS-2.2.1",)),
    "secret.hardcoded": PolicyRule(Severity.CRITICAL, ()),
    "provider.static_credentials": PolicyRule(Severity.CRITICAL, ("CIS-AWS-1.4",)),
    "logging.cloudtrail.absent": PolicyRule(Severity.MEDIUM, ("CIS-AWS-3.1",)),
}

UNMAPPED_RULE_CLASS_PREFIX = "checkov"


def plimsoll_rule(rule_class: str) -> PolicyRule:
    """Severity and benchmark refs for a rule class from the Rego pack.

    An unmapped class is a rule from someone's own `--policy-dir`, which is a supported
    thing to have. It is reported at the same below-threshold default an unmapped checkov
    check gets: visible, but not able to fail somebody else's build on its own.
    """
    return PLIMSOLL.get(rule_class, PolicyRule(Severity.MEDIUM, ()))


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
