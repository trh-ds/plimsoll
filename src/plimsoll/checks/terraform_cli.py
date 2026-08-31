"""Shared terraform plumbing for the `validate` and `plan` adapters.

Two adapters shell out to the same binary against the same directory. Where they run,
whether the workspace was initialised, and which environment they run under has to be
one decision, made once per gate run -- otherwise `validate` and `plan` can disagree
about the same files and the report contains both answers.

Three things here are load-bearing:

**The workspace is never written to.** `terraform init` writes `.terraform/` and
`.terraform.lock.hcl` into its working directory. `plimsoll` reports; it does not edit
your repository, and that promise does not have an exception for "but terraform needs
it". So when the workspace is not already initialised, terraform runs against a
throwaway copy in the run's scratch directory. When it *is* already initialised, we
run in place and skip init entirely -- the user did the work, and re-doing it in a copy
would download hundreds of megabytes of providers to reach the same answer.

**Provider downloads are cached across runs.** Re-verification re-runs the gate once
per patch attempt. Without `TF_PLUGIN_CACHE_DIR` that is a fresh provider download per
attempt, which turns a thirty-second verification into a five-minute one.

**`terraform apply` is never invoked.** Not here, not anywhere. This module runs
`version`, `init`, `validate`, `plan`, and `show`, and that list is exhaustive.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from plimsoll.core.gate import CheckContext, CheckExecutionError

BINARY = "terraform"
VERSION_TIMEOUT_SECONDS = 30

# Copied into the throwaway working directory; everything else is left behind.
_SKIP = shutil.ignore_patterns(
    ".terraform", ".git", ".pytest_cache", "__pycache__", "*.tfstate", "*.tfstate.backup"
)


def executable() -> str | None:
    return shutil.which(BINARY)


def require_executable() -> str:
    found = executable()
    if found is None:
        raise CheckExecutionError(f"{BINARY} disappeared from PATH between probe and run")
    return found


def parse_version(stdout: str) -> str | None:
    """Read the version from `terraform version -json`. None when it cannot be read."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("terraform_version")
    return str(version) if version else None


def plugin_cache_dir() -> Path:
    """Where downloaded providers are kept between runs.

    Honours `TF_PLUGIN_CACHE_DIR` when the user set one; otherwise a per-user cache.
    Shared deliberately: the verifier re-runs the gate once per patch attempt, and
    without a cache each attempt re-downloads the AWS provider.
    """
    configured = os.environ.get("TF_PLUGIN_CACHE_DIR")
    if configured:
        return Path(configured)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "plimsoll" / "terraform-plugins"


def base_env() -> dict[str, str]:
    """The environment terraform runs under. Never contains real credentials we invented."""
    env = dict(os.environ)
    env["TF_IN_AUTOMATION"] = "1"
    env["TF_INPUT"] = "0"
    env["CHECKPOINT_DISABLE"] = "1"
    cache = plugin_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    env["TF_PLUGIN_CACHE_DIR"] = str(cache)
    return env


def has_real_credentials() -> bool:
    """True when the caller already has AWS credentials we should use as they are.

    A CI job running under a real read-only role produces a more accurate plan than any
    placeholder can, and this project has no business silently swapping out a caller's
    identity. When this is true nothing below is applied.
    """
    if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"):
        return True
    # The container-credentials and web-identity paths CI runners use.
    return bool(
        os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
        or os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE")
    )


def with_dummy_credentials(env: dict[str, str]) -> dict[str, str]:
    """Fill in placeholder AWS credentials, only where none are set.

    `terraform plan` on resources that do not exist yet needs credentials to be
    *present*, not valid: with `-refresh=false` there is nothing to look up. Placeholders
    keep the plan path working on a laptop with no AWS account, which is the point.

    Placeholders alone are not enough -- see `credential_override` for the other half.

    For configurations that genuinely reach the API at plan time (a `data` source reading
    a real AMI, most obviously), point the provider at LocalStack instead; see
    `architecture.md`.
    """
    placeholders = {
        "AWS_ACCESS_KEY_ID": "AKIAPLIMSOLLDUMMYKEY0",
        "AWS_SECRET_ACCESS_KEY": "plimsoll-placeholder-not-a-real-secret",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_REGION": "us-east-1",
        # The SDK otherwise spends its connect timeout trying to reach the EC2 instance
        # metadata service, which is not there on a laptop.
        "AWS_EC2_METADATA_DISABLED": "true",
    }
    for key, value in placeholders.items():
        env.setdefault(key, value)
    return env


OVERRIDE_FILENAME = "plimsoll_provider_override.tf"

CREDENTIAL_OVERRIDE = """\
# Written by plimsoll into a throwaway copy of your configuration. It is never written to
# your repository, and never written at all when real AWS credentials are present.
#
# Why it exists: the AWS provider calls sts:GetCallerIdentity before it will plan
# anything. With placeholder credentials that call returns InvalidClientTokenId, and the
# whole plan fails -- which would be reported as a finding about your configuration when
# it is really a fact about this machine having no AWS account.
#
# These three arguments turn off checks about *the caller*. None of them changes how any
# resource in your configuration is planned, which is the only thing the rule pack reads.
provider "aws" {
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_region_validation      = true
}
"""

_PROVIDER_AWS = re.compile(r'^\s*provider\s+"aws"\s*\{')
_ALIAS = re.compile(r"^\s*alias\s*=")


def has_default_aws_provider(workspace: Path) -> bool:
    """True when a `provider "aws"` block with no `alias` is declared here.

    Terraform's override files merge into an existing block and error when there is none
    to merge into, so this has to be checked rather than assumed. An aliased-only setup
    is not overridable, and gets the honest plan failure instead of a broken one.
    """
    for path in sorted(workspace.rglob("*.tf")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for index, line in enumerate(lines):
            if not _PROVIDER_AWS.match(line):
                continue
            depth = 0
            for body in lines[index:]:
                depth += body.count("{") - body.count("}")
                if _ALIAS.match(body):
                    break
                if depth <= 0:
                    return True
    return False


def _run(
    args: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [require_executable(), *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CheckExecutionError(f"{BINARY} {args[0]} exceeded {timeout}s") from exc
    except OSError as exc:
        raise CheckExecutionError(f"could not execute {BINARY} {args[0]}: {exc}") from exc


def needs_credential_override(workspace: Path) -> bool:
    return not has_real_credentials() and has_default_aws_provider(workspace)


def _initialise(ctx: CheckContext) -> Path:
    """Return the directory terraform should run in, initialised.

    Raises `CheckExecutionError` when init fails -- offline, a provider that does not
    exist, a bad `required_providers` block. Every adapter that asks gets the same
    failure, because `CheckContext.once` caches it.

    ponytail: every gate run re-inits a fresh copy, which for the verifier means once per
    patch attempt. `TF_PLUGIN_CACHE_DIR` keeps that to a local copy rather than a
    download. If it becomes the bottleneck, the upgrade is a `TF_DATA_DIR` shared across
    runs and keyed by the workspace's provider requirements.
    """
    if not needs_credential_override(ctx.workspace) and (ctx.workspace / ".terraform").is_dir():
        # Already initialised by the caller, and no injected file is needed. Use it as it
        # stands: re-initialising a copy would fetch the providers again for the same
        # answer.
        return ctx.workspace

    workdir = ctx.scratch / "terraform"
    shutil.copytree(ctx.workspace, workdir, ignore=_SKIP, symlinks=False)
    if needs_credential_override(ctx.workspace):
        (workdir / OVERRIDE_FILENAME).write_text(CREDENTIAL_OVERRIDE, encoding="utf-8")

    completed = _run(
        ["init", "-input=false", "-backend=false", "-no-color"],
        workdir,
        base_env(),
        ctx.deadline.cap(ctx.config.timeout_seconds),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[:400]
        raise CheckExecutionError(
            f"`{BINARY} init` failed, so nothing could be verified: {detail}"
        )
    return workdir


def workdir(ctx: CheckContext) -> Path:
    """The initialised directory terraform runs in for this gate run. Produced once."""
    return ctx.once("terraform_workdir", lambda: _initialise(ctx))


def run_terraform(
    ctx: CheckContext,
    args: list[str],
    *,
    dummy_credentials: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run one terraform subcommand in the run's working directory."""
    env = base_env()
    if dummy_credentials:
        env = with_dummy_credentials(env)
    return _run(args, workdir(ctx), env, ctx.deadline.cap(ctx.config.timeout_seconds))
