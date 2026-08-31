"""Typer entrypoint.

`scan`, `verify`, `explain`, and `doctor`. `exceptions` needs the exception store, which
is Phase 3, and a subcommand that exists but does nothing is worse than one that is
honestly absent.

Exit codes come from the gate and the decisions, never from anything a model said.
"""

from __future__ import annotations

import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

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
    # Order matters. A run where nothing could be verified reports that, even when it
    # collected no findings -- especially then. A broken toolchain must not be able to
    # masquerade as a clean pass.
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
    if not result.gate.any_check_ran:
        return ExitCode.NOTHING_RAN
    if result.unresolved_at_or_above:
        return ExitCode.FINDINGS
    return ExitCode.CLEAN


def _validate_common(path: Path, policy_dir: Path | None, timeout: int) -> None:
    if not path.is_dir():
        typer.echo(f"not a directory: {path}", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)
    if policy_dir is not None and not policy_dir.is_dir():
        typer.echo(f"policy directory does not exist: {policy_dir}", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)
    if timeout <= 0:
        typer.echo("--timeout must be greater than zero", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)


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
    timeout: Annotated[int, typer.Option("--timeout", help="Per-check timeout in seconds.")] = 120,
) -> None:
    """Run the deterministic gate. No model is involved."""
    _validate_common(path, policy_dir, timeout)

    config = GateConfig(
        fail_on=fail_on,
        check=CheckConfig(timeout_seconds=timeout, policy_dir=policy_dir),
    )
    result = default_gate().run(path, config)

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
    no_llm: bool,
    max_retries: int,
    max_llm_findings: int,
    explain_only: bool,
) -> None:
    _validate_common(path, policy_dir, timeout)
    if max_retries < 0:
        typer.echo("--max-retries cannot be negative", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)
    if max_llm_findings <= 0:
        typer.echo("--max-llm-findings must be greater than zero", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)

    config = GateConfig(
        fail_on=fail_on,
        check=CheckConfig(timeout_seconds=timeout, policy_dir=policy_dir),
    )
    provider, reason = _resolve_provider(no_llm)
    gate: Gate = default_gate()

    loop = VerifierLoop(
        gate,
        config,
        provider,
        max_retries=max_retries,
        max_findings=max_llm_findings,
    )
    result = loop.run(path, explain_only=explain_only)
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
    timeout: Annotated[int, typer.Option("--timeout", help="Per-check timeout in seconds.")] = 120,
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
    timeout: Annotated[int, typer.Option("--timeout", help="Per-check timeout in seconds.")] = 120,
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
        no_llm=False,
        max_retries=0,
        max_llm_findings=max_llm_findings,
        explain_only=True,
    )


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
