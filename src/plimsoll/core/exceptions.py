"""Recorded exceptions: the "we looked at this and accepted it" register.

A policy tool without a way to say no to itself gets switched off. The alternative to
this file is a `# nosec`-style comment in the Terraform, which is invisible to review,
carries no expiry, and names nobody. An entry here is committed, diffed, and approved
like any other change.

Four properties are load-bearing:

**A missing store is an empty store.** Most repositories have no exceptions, and making
that an error would mean every user meets a failure before they meet a finding.

**A malformed store is an error.** This is the one place where being lenient is
dangerous: a typo that silently drops every entry re-raises findings a reviewer signed
off on, and the run would look like the reviewer was wrong rather than like the file was.

**An expired entry does not exempt.** It attaches to the decision anyway, so the report
can say the sign-off lapsed. A reviewer who believed this was handled deserves to be
told it ran out, not to rediscover the finding as though it were new.

**An exempted finding never reaches a model.** The loop matches before it targets. That
ordering is not an optimisation: an accepted violation must not cost a token and must
not leave the machine.
"""

from __future__ import annotations

from datetime import date
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from plimsoll.core.models import ExceptionRef, Finding

STORE_RELATIVE_PATH = Path(".plimsoll") / "exceptions.yaml"
SCHEMA_VERSION = "0.1"


class ExceptionStoreError(RuntimeError):
    """The exception file exists and could not be read as one. Fatal, deliberately."""


class ExceptionEntry(BaseModel):
    """One accepted violation, per the YAML shape in `specifications.md`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_class: str = Field(min_length=1)
    resource_address: str = Field(min_length=1)
    justification: str = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    approved_in: str | None = None
    created: date | None = None
    expires: date | None = None

    def is_expired(self, today: date | None = None) -> bool:
        """`expires` is the day the exception lapses *on*, not its last valid day.

        The boundary is `<=` rather than `<` so `plimsoll exceptions expire` takes effect
        the moment it is run, and so the ambiguous case fails closed: an exception whose
        date is today stops exempting today. Erring the other way would keep a lapsed
        sign-off in force for one more day, which is the wrong direction for a tool whose
        whole argument is that silence must not read as success.
        """
        if self.expires is None:
            return False
        return self.expires <= (today or date.today())

    def matches(self, finding: Finding) -> bool:
        """Exact on `rule_class`, glob on `resource_address`.

        `fnmatchcase` rather than `fnmatch`: resource addresses are case-sensitive in
        Terraform, and the platform-dependent case folding `fnmatch` does on Windows
        would make the same store behave differently on a developer's laptop and in CI.
        """
        if self.rule_class != finding.rule_class:
            return False
        return fnmatchcase(finding.resource_address, self.resource_address)

    def to_ref(self, today: date | None = None) -> ExceptionRef:
        return ExceptionRef(
            rule_class=self.rule_class,
            resource_address=self.resource_address,
            justification=self.justification,
            approved_by=self.approved_by,
            approved_in=self.approved_in,
            created=self.created,
            expires=self.expires,
            expired=self.is_expired(today),
        )


class ExceptionStore:
    """The entries for one workspace."""

    def __init__(
        self, entries: list[ExceptionEntry] | None = None, path: Path | None = None
    ) -> None:
        self.entries = list(entries or [])
        self.path = path

    def __len__(self) -> int:
        return len(self.entries)

    @classmethod
    def path_for(cls, workspace: Path) -> Path:
        return workspace / STORE_RELATIVE_PATH

    @classmethod
    def load(cls, workspace: Path) -> ExceptionStore:
        path = cls.path_for(workspace)
        if not path.is_file():
            return cls(path=path)
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ExceptionStoreError(f"{path} could not be read: {exc}") from exc
        return cls(parse_entries(payload, path), path=path)

    def match(self, finding: Finding, today: date | None = None) -> ExceptionRef | None:
        """The exception this finding matched, or None.

        A live entry wins over an expired one: two entries can cover the same finding
        when someone renewed an exception without deleting the old row, and reporting the
        stale one as lapsed would be wrong.
        """
        matched = [e for e in self.entries if e.matches(finding)]
        if not matched:
            return None
        live = next((e for e in matched if not e.is_expired(today)), None)
        return (live or matched[0]).to_ref(today)

    def save(self, path: Path | None = None) -> Path:
        target = path or self.path
        if target is None:  # pragma: no cover - callers always have a workspace
            raise ExceptionStoreError("no path to write the exception store to")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dump_entries(self.entries), encoding="utf-8")
        self.path = target
        return target


def parse_entries(payload: object, path: Path) -> list[ExceptionEntry]:
    """Validate the YAML document. Every failure here is an `ExceptionStoreError`.

    An empty file is an empty store -- `yaml.safe_load("")` is None, and a file someone
    created and has not filled in yet is not malformed.
    """
    if payload is None:
        return []
    if not isinstance(payload, dict):
        raise ExceptionStoreError(f"{path}: expected a mapping at the top level")

    version = str(payload.get("version") or SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ExceptionStoreError(
            f"{path}: version {version!r} is not supported (this build reads {SCHEMA_VERSION!r})"
        )

    raw = payload.get("exceptions") or []
    if not isinstance(raw, list):
        raise ExceptionStoreError(f"{path}: 'exceptions' must be a list")

    entries: list[ExceptionEntry] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ExceptionStoreError(f"{path}: exception {index} is not a mapping")
        try:
            entries.append(ExceptionEntry.model_validate(item))
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'entry'}: {err['msg']}"
                for err in exc.errors()
            )
            raise ExceptionStoreError(
                f"{path}: exception {index} is invalid -- {problems}"
            ) from exc
    return entries


def dump_entries(entries: list[ExceptionEntry]) -> str:
    """Serialise the store. Comments in the original file are not preserved.

    ponytail: round-tripping comments needs ruamel.yaml, a dependency for a subcommand
    most users will never run -- they will edit this file by hand. Add it when someone
    reports losing a comment to `plimsoll exceptions add`.
    """
    document: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "exceptions": [e.model_dump(mode="json", exclude_none=True) for e in entries],
    }
    return yaml.safe_dump(document, sort_keys=False, default_flow_style=False, allow_unicode=True)
