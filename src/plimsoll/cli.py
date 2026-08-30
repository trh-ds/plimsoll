"""Typer entrypoint.

Only `scan` and `doctor` exist. `verify`, `explain`, and `exceptions` need the reasoning
loop, the exception store, and provenance, none of which are built -- and a subcommand
that exists but does nothing is worse than one that is honestly absent.
"""

from __future__ import annotations

import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from plimsoll.core.gate import default_gate
from plimsoll.core.models import CheckConfig, GateConfig, GateResult, Severity
from plimsoll.reporters import render_json, render_terminal


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


def _render(result: GateResult, fmt: OutputFormat, fail_on: Severity) -> str:
    if fmt is OutputFormat.JSON:
        return render_json(result, fail_on=fail_on)
    return render_terminal(result, fail_on=fail_on)


def _exit_code(result: GateResult, fail_on: Severity) -> int:
    # Order matters. A run where nothing could be verified reports that, even when it
    # collected no findings -- especially then. A broken toolchain must not be able to
    # masquerade as a clean pass.
    if not result.any_check_ran:
        return ExitCode.NOTHING_RAN
    if result.findings_at_or_above(fail_on):
        return ExitCode.FINDINGS
    return ExitCode.CLEAN


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
    if not path.is_dir():
        typer.echo(f"not a directory: {path}", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)
    if policy_dir is not None and not policy_dir.is_dir():
        typer.echo(f"policy directory does not exist: {policy_dir}", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)
    if timeout <= 0:
        typer.echo("--timeout must be greater than zero", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)

    config = GateConfig(
        fail_on=fail_on,
        check=CheckConfig(timeout_seconds=timeout, policy_dir=policy_dir),
    )
    result = default_gate().run(path, config)

    typer.echo(_render(result, output_format, fail_on))
    raise typer.Exit(_exit_code(result, fail_on))


@app.command()
def doctor() -> None:
    """Report which external tools are available."""
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
    if available:
        typer.echo(f"{available} of {len(gate.checks)} checks can run.")
        return
    typer.echo("No checks can run. A scan would verify nothing.")
    raise typer.Exit(ExitCode.NOTHING_RAN)


def main() -> None:
    sys.exit(app())


if __name__ == "__main__":
    main()
