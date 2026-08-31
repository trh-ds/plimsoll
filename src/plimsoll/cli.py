"""Typer entrypoint.

`scan`, `verify`, `explain`, `exceptions`, and `doctor`.

Exit codes come from the gate and the decisions, never from anything a model said.
"""

from __future__ import annotations

import sys
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from plimsoll.core.deadline import Deadline
from plimsoll.core.exceptions import ExceptionEntry, ExceptionStore, ExceptionStoreError
from plimsoll.core.gate import Gate, default_gate
from plimsoll.core.loop import VerifierLoop
from plimsoll.core.models import (
    CheckConfig,
    GateConfig,
    GateResult,
    Severity,
    VerifyResult,
)
from plimsoll.llm import LLMConfig, LLMError, LLMProvider, build_provider
from plimsoll.reporters import (
    render_json,
    render_terminal,
    render_verify_json,
    render_verify_terminal,
)


class ExitCode:
    """From the CLI contract in specifications.md."""

    CLEAN = 0
    FINDINGS = 1
    CONFIG_ERROR = 2
    NOTHING_RAN = 3
    TIMED_OUT = 4


DEFAULT_RUN_TIMEOUT_SECONDS = 600
DEFAULT_CHECK_TIMEOUT_SECONDS = 120

_TIMEOUT_HELP = "Whole-run budget in seconds."
_CHECK_TIMEOUT_HELP = "Per-subprocess timeout in seconds, capped by --timeout."


class OutputFormat(StrEnum):
    TERMINAL = "terminal"
    JSON = "json"


app = typer.Typer(
    name="plimsoll",
    help="Verification for AI-generated infrastructure code.",
    no_args_is_help=True,
    add_completion=False,
)


def _exit_code(result: GateResult, fail_on: Severity) -> int:
    # Order matters, and timing out comes first: it is the reason the rest of the run is
    # incomplete, so reporting anything else would bury the cause. A run where nothing
    # could be verified reports that next, even when it collected no findings --
    # especially then. A broken toolchain must not masquerade as a clean pass, and
    # neither may a truncated one.
    if result.timed_out:
        return ExitCode.TIMED_OUT
    if not result.any_check_ran:
        return ExitCode.NOTHING_RAN
    if result.findings_at_or_above(fail_on):
        return ExitCode.FINDINGS
    return ExitCode.CLEAN


def _verify_exit_code(result: VerifyResult, fail_on: Severity) -> int:
    """Like `_exit_code`, but a verified fix clears the finding it fixed.

    Derived from `Decision.status`, which only `VerifierLoop` sets and which cannot be
    `FIXED` without a `PatchVerification` the gate produced.
    """
    if result.timed_out:
        return ExitCode.TIMED_OUT
    if not result.gate.any_check_ran:
        return ExitCode.NOTHING_RAN
    if result.unresolved_at_or_above:
        return ExitCode.FINDINGS
    return ExitCode.CLEAN


def _validate_common(path: Path, policy_dir: Path | None, timeout: int, check_timeout: int) -> None:
    if not path.is_dir():
        typer.echo(f"not a directory: {path}", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)
    if policy_dir is not None and not policy_dir.is_dir():
        typer.echo(f"policy directory does not exist: {policy_dir}", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)
    for flag, value in (("--timeout", timeout), ("--check-timeout", check_timeout)):
        if value <= 0:
            typer.echo(f"{flag} must be greater than zero", err=True)
            raise typer.Exit(ExitCode.CONFIG_ERROR)


def _load_exceptions(path: Path) -> ExceptionStore:
    """Read `.plimsoll/exceptions.yaml`, or exit 2.

    A missing store is empty and silent. A malformed one is fatal: a typo that dropped
    every entry would re-raise findings a reviewer signed off on, and the run would look
    like the reviewer was wrong rather than like the file was.
    """
    try:
        return ExceptionStore.load(path)
    except ExceptionStoreError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR) from exc


def _resolve_provider(no_llm: bool) -> tuple[LLMProvider | None, str | None]:
    """Build the configured provider, or explain why there is none.

    Never fatal. A missing key or an unreachable router degrades the run to a
    deterministic scan (invariant 4); it does not stop it and it does not fail open.
    """
    if no_llm:
        return None, "--no-llm"
    config = LLMConfig.from_env()
    try:
        provider = build_provider(config)
    except LLMError as exc:
        return None, str(exc)

    availability = provider.is_available()
    if not availability.available:
        return None, availability.reason or "provider is not configured"
    return provider, None


@app.command()
def scan(
    path: Annotated[Path, typer.Argument(help="Directory of Terraform to scan.")],
    fail_on: Annotated[
        Severity,
        typer.Option("--fail-on", help="Minimum severity that sets a non-zero exit."),
    ] = Severity.HIGH,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", help="Report format.")
    ] = OutputFormat.TERMINAL,
    policy_dir: Annotated[
        Path | None,
        typer.Option("--policy-dir", help="Additional policy directory."),
    ] = None,
    timeout: Annotated[
        int, typer.Option("--timeout", help=_TIMEOUT_HELP)
    ] = DEFAULT_RUN_TIMEOUT_SECONDS,
    check_timeout: Annotated[
        int, typer.Option("--check-timeout", help=_CHECK_TIMEOUT_HELP)
    ] = DEFAULT_CHECK_TIMEOUT_SECONDS,
) -> None:
    """Run the deterministic gate. No model is involved."""
    _validate_common(path, policy_dir, timeout, check_timeout)

    config = GateConfig(
        fail_on=fail_on,
        check=CheckConfig(timeout_seconds=check_timeout, policy_dir=policy_dir),
    )
    result = default_gate().run(path, config, Deadline(timeout))

    if output_format is OutputFormat.JSON:
        typer.echo(render_json(result, fail_on=fail_on))
    else:
        typer.echo(render_terminal(result, fail_on=fail_on))
    raise typer.Exit(_exit_code(result, fail_on))


def _run_loop(
    path: Path,
    fail_on: Severity,
    output_format: OutputFormat,
    policy_dir: Path | None,
    timeout: int,
    check_timeout: int,
    no_llm: bool,
    max_retries: int,
    max_llm_findings: int,
    explain_only: bool,
) -> None:
    _validate_common(path, policy_dir, timeout, check_timeout)
    if max_retries < 0:
        typer.echo("--max-retries cannot be negative", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)
    if max_llm_findings <= 0:
        typer.echo("--max-llm-findings must be greater than zero", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)

    config = GateConfig(
        fail_on=fail_on,
        check=CheckConfig(timeout_seconds=check_timeout, policy_dir=policy_dir),
    )
    provider, reason = _resolve_provider(no_llm)
    gate: Gate = default_gate()

    loop = VerifierLoop(
        gate,
        config,
        provider,
        max_retries=max_retries,
        max_findings=max_llm_findings,
        exceptions=_load_exceptions(path),
    )
    result = loop.run(path, explain_only=explain_only, deadline=Deadline(timeout))
    if provider is None and reason:
        result.llm_unavailable_reason = reason

    if output_format is OutputFormat.JSON:
        typer.echo(render_verify_json(result, fail_on=fail_on))
    else:
        typer.echo(render_verify_terminal(result, fail_on=fail_on))
    raise typer.Exit(_verify_exit_code(result, fail_on))


@app.command()
def verify(
    path: Annotated[Path, typer.Argument(help="Directory of Terraform to verify.")],
    fail_on: Annotated[
        Severity, typer.Option("--fail-on", help="Minimum severity that sets a non-zero exit.")
    ] = Severity.HIGH,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", help="Report format.")
    ] = OutputFormat.TERMINAL,
    policy_dir: Annotated[
        Path | None, typer.Option("--policy-dir", help="Additional policy directory.")
    ] = None,
    timeout: Annotated[
        int, typer.Option("--timeout", help=_TIMEOUT_HELP)
    ] = DEFAULT_RUN_TIMEOUT_SECONDS,
    check_timeout: Annotated[
        int, typer.Option("--check-timeout", help=_CHECK_TIMEOUT_HELP)
    ] = DEFAULT_CHECK_TIMEOUT_SECONDS,
    no_llm: Annotated[
        bool, typer.Option("--no-llm", help="Deterministic mode. Same as scan, with decisions.")
    ] = False,
    max_retries: Annotated[
        int, typer.Option("--max-retries", help="Patch attempts per finding before escalation.")
    ] = 2,
    max_llm_findings: Annotated[
        int,
        typer.Option("--max-llm-findings", help="Cap on findings sent to the model."),
    ] = 25,
) -> None:
    """Scan, draft fixes, re-verify them through the same gate, escalate what fails.

    The workspace is never modified: patches are applied to a temporary copy. Only
    fixes that pass re-verification are shown.
    """
    _run_loop(
        path,
        fail_on,
        output_format,
        policy_dir,
        timeout,
        check_timeout,
        no_llm,
        max_retries,
        max_llm_findings,
        explain_only=False,
    )


@app.command()
def explain(
    path: Annotated[Path, typer.Argument(help="Directory of Terraform to explain.")],
    fail_on: Annotated[
        Severity, typer.Option("--fail-on", help="Minimum severity that sets a non-zero exit.")
    ] = Severity.HIGH,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", help="Report format.")
    ] = OutputFormat.TERMINAL,
    policy_dir: Annotated[
        Path | None, typer.Option("--policy-dir", help="Additional policy directory.")
    ] = None,
    timeout: Annotated[
        int, typer.Option("--timeout", help=_TIMEOUT_HELP)
    ] = DEFAULT_RUN_TIMEOUT_SECONDS,
    check_timeout: Annotated[
        int, typer.Option("--check-timeout", help=_CHECK_TIMEOUT_HELP)
    ] = DEFAULT_CHECK_TIMEOUT_SECONDS,
    max_llm_findings: Annotated[
        int, typer.Option("--max-llm-findings", help="Cap on findings sent to the model.")
    ] = 25,
) -> None:
    """Scan and explain the findings in prose. No patches are drafted."""
    _run_loop(
        path,
        fail_on,
        output_format,
        policy_dir,
        timeout,
        check_timeout,
        no_llm=False,
        max_retries=0,
        max_llm_findings=max_llm_findings,
        explain_only=True,
    )


exceptions_app = typer.Typer(
    name="exceptions",
    help="Inspect and edit the recorded exceptions in .plimsoll/exceptions.yaml.",
    no_args_is_help=True,
)
app.add_typer(exceptions_app)

_WORKSPACE = Annotated[Path, typer.Argument(help="Repository root holding .plimsoll/.")]


def _open_store(path: Path) -> ExceptionStore:
    if not path.is_dir():
        typer.echo(f"not a directory: {path}", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)
    return _load_exceptions(path)


def _describe_entry(entry: ExceptionEntry, today: date) -> str:
    state = "EXPIRED" if entry.is_expired(today) else "live"
    until = f", expires {entry.expires.isoformat()}" if entry.expires else ", no expiry"
    return (
        f"  {state:<8} {entry.rule_class:<34} {entry.resource_address}\n"
        f"           {entry.justification}\n"
        f"           approved by {entry.approved_by}"
        f"{f' in {entry.approved_in}' if entry.approved_in else ''}{until}"
    )


@exceptions_app.command("list")
def exceptions_list(path: _WORKSPACE = Path(".")) -> None:
    """Show every recorded exception, marking the ones that have lapsed."""
    store = _open_store(path)
    if not store.entries:
        typer.echo(f"No exceptions recorded in {ExceptionStore.path_for(path)}.")
        return
    today = date.today()
    for entry in store.entries:
        typer.echo(_describe_entry(entry, today))
        typer.echo("")
    expired = sum(1 for e in store.entries if e.is_expired(today))
    typer.echo(f"{len(store.entries)} exception(s), {expired} expired.")


@exceptions_app.command("add")
def exceptions_add(
    rule_class: Annotated[str, typer.Option("--rule-class", help="Exact rule class to exempt.")],
    resource_address: Annotated[
        str, typer.Option("--resource", help="Resource address; glob patterns are allowed.")
    ],
    justification: Annotated[
        str, typer.Option("--justification", help="Why this violation is accepted.")
    ],
    approved_by: Annotated[str, typer.Option("--approved-by", help="Who accepted it.")],
    approved_in: Annotated[
        str | None, typer.Option("--approved-in", help="Where it was accepted, e.g. 'PR #442'.")
    ] = None,
    expires: Annotated[
        str | None, typer.Option("--expires", help="ISO date the exception lapses on.")
    ] = None,
    path: _WORKSPACE = Path("."),
) -> None:
    """Record a new exception."""
    store = _open_store(path)
    try:
        entry = ExceptionEntry(
            rule_class=rule_class,
            resource_address=resource_address,
            justification=justification,
            approved_by=approved_by,
            approved_in=approved_in,
            created=date.today(),
            expires=date.fromisoformat(expires) if expires else None,
        )
    except ValueError as exc:
        typer.echo(f"invalid exception: {exc}", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR) from exc

    store.entries.append(entry)
    typer.echo(f"Recorded {entry.rule_class} on {entry.resource_address} in {store.save()}.")


@exceptions_app.command("expire")
def exceptions_expire(
    rule_class: Annotated[str, typer.Option("--rule-class", help="Rule class to expire.")],
    resource_address: Annotated[
        str, typer.Option("--resource", help="Resource address, matched exactly as written.")
    ],
    path: _WORKSPACE = Path("."),
) -> None:
    """End an exception today, without deleting the record of who approved it."""
    store = _open_store(path)
    today = date.today()
    matched = [
        e
        for e in store.entries
        if e.rule_class == rule_class and e.resource_address == resource_address
    ]
    if not matched:
        typer.echo(f"no exception matches {rule_class} on {resource_address}", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)

    # `expires` is the day the exception lapses on, so today takes effect immediately.
    store.entries = [
        e.model_copy(update={"expires": today}) if e in matched else e for e in store.entries
    ]
    typer.echo(f"Expired {len(matched)} exception(s) as of {today.isoformat()} in {store.save()}.")


@exceptions_app.command("prune")
def exceptions_prune(path: _WORKSPACE = Path(".")) -> None:
    """Delete expired entries."""
    store = _open_store(path)
    today = date.today()
    keep = [e for e in store.entries if not e.is_expired(today)]
    removed = len(store.entries) - len(keep)
    if not removed:
        typer.echo("Nothing to prune; no exception has expired.")
        return
    store.entries = keep
    typer.echo(f"Pruned {removed} expired exception(s) from {store.save()}.")


@app.command()
def doctor() -> None:
    """Report which external tools and which model provider are available."""
    gate = default_gate()
    available = 0
    for check in gate.checks:
        status = check.is_available()
        if status.available:
            available += 1
            version = status.version or "version unknown"
            typer.echo(f"ok       {check.name:<20} {status.tool} ({version})")
        else:
            typer.echo(f"missing  {check.name:<20} {status.reason}")

    typer.echo("")
    provider, reason = _resolve_provider(no_llm=False)
    if provider is not None:
        typer.echo(f"ok       {'model':<20} {provider.describe()}")
        typer.echo("         (not contacted; doctor does not spend tokens)")
    else:
        typer.echo(f"missing  {'model':<20} {reason}")
        typer.echo("         scan still works; verify reports deterministically only.")

    typer.echo("")
    if available:
        typer.echo(f"{available} of {len(gate.checks)} checks can run.")
        return
    typer.echo("No checks can run. A scan would verify nothing.")
    raise typer.Exit(ExitCode.NOTHING_RAN)


def main() -> None:
    sys.exit(app())


if __name__ == "__main__":
    main()
