"""Per-repository settings: `.plimsoll/config.yaml`.

A CI job, a pre-commit hook, and a developer's terminal should judge a repository the
same way without each of them repeating the same six flags. This file is where a
repository says how it wants to be judged; a flag on the command line still wins, so a
one-off run never needs the file edited.

Strict about its own shape for the same reason the exception store is. An unknown key is
an error rather than ignored: `fail-on: low` with a hyphen, silently dropped, leaves the
gate at HIGH while everyone believes it is at LOW. A policy tool that quietly ignores its
configuration fails open in the most confusing possible way.

Paths in the file are relative to the workspace, not to wherever the command was run
from, so the file means the same thing in CI and on a laptop.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from plimsoll.core.models import Severity

CONFIG_RELATIVE_PATH = Path(".plimsoll") / "config.yaml"
SCHEMA_VERSION = "0.1"


class ConfigError(RuntimeError):
    """The config file exists and could not be read as one. Fatal: exit 2."""


class ProjectConfig(BaseModel):
    """Every field optional. None means "not set here; use the flag or the default"."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = SCHEMA_VERSION
    fail_on: Severity | None = None
    timeout: int | None = Field(default=None, gt=0)
    check_timeout: int | None = Field(default=None, gt=0)
    policy_dir: Path | None = None
    baseline: Path | None = None
    strict: bool | None = None
    max_retries: int | None = Field(default=None, ge=0)
    max_llm_findings: int | None = Field(default=None, gt=0)

    @field_validator("fail_on", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        # `fail_on: high` is what people write; the enum's values are upper case, and the
        # CLI flag already accepts either.
        return value.upper() if isinstance(value, str) else value

    def resolved(self, workspace: Path) -> ProjectConfig:
        """Anchor relative paths to the workspace."""
        update: dict[str, Path] = {}
        for name in ("policy_dir", "baseline"):
            value = getattr(self, name)
            if value is not None and not value.is_absolute():
                update[name] = workspace / value
        return self.model_copy(update=update)


def config_path(workspace: Path) -> Path:
    return workspace / CONFIG_RELATIVE_PATH


def load_project_config(workspace: Path, explicit: Path | None = None) -> ProjectConfig:
    """Read the workspace's config, or `explicit` when one was named.

    A missing default file is an empty config: most repositories will never have one.
    A missing file that was named on the command line is an error, because whoever named
    it expected it to change something.
    """
    path = explicit or config_path(workspace)
    if not path.is_file():
        if explicit is not None:
            raise ConfigError(f"config file {path} does not exist")
        return ProjectConfig()

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path} could not be read: {exc}") from exc
    if payload is None:
        return ProjectConfig()
    if not isinstance(payload, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")

    version = str(payload.get("version") or SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ConfigError(
            f"{path}: version {version!r} is not supported (this build reads {SCHEMA_VERSION!r})"
        )
    try:
        config = ProjectConfig.model_validate({**payload, "version": version})
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'config'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"{path} is invalid -- {problems}") from exc
    # Relative to the workspace, whichever file was read: a config passed with --config
    # from elsewhere still describes the workspace being judged.
    return config.resolved(workspace)
