"""Typer entrypoint.

`scan`, `verify`, `explain`, `baseline`, `exceptions`, and `doctor`.

Exit codes come from the gate and the decisions, never from anything a model said.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, TypeVar

import typer

from plimsoll.core.baseline import (
    DEFAULT_RELATIVE_PATH as DEFAULT_BASELINE_PATH,
)
from plimsoll.core.baseline import (
    Baseline,
    BaselineError,
    load_baseline,
    write_baseline,
)
from plimsoll.core.config import ConfigError, load_project_config
from plimsoll.core.deadline import Deadline
from plimsoll.core.exceptions import (
    ExceptionEntry,
    ExceptionStore,
    ExceptionStoreError,
    suppress_exempted,
)
from plimsoll.core.gate import Gate, default_gate
from plimsoll.core.loop import VerifierLoop
from plimsoll.core.models import (
    CheckConfig,
    GateConfig,
    GateResult,
    Severity,
    VerifyResult,
)
from plimsoll.core.provenance import (
    ModelIdentity,
    build_record,
    gate_as_verify_result,
    write_record,
)
from plimsoll.llm import LLMConfig, LLMError, LLMProvider, build_provider
from plimsoll.reporters import (
    render_github,
    render_json,
    render_junit,
    render_sarif,
    render_terminal,
    render_verify_github,
    render_verify_json,
    render_verify_junit,
    render_verify_sarif,
    render_verify_terminal,
)


class ExitCode:
    """From the CLI contract in specifications.md."""

    CLEAN = 0
    FINDINGS = 1
    CONFIG_ERROR = 2
    # Nothing was verified -- or, under --strict, not everything was.
    NOTHING_RAN = 3
    TIMED_OUT = 4


DEFAULT_RUN_TIMEOUT_SECONDS = 600
DEFAULT_CHECK_TIMEOUT_SECONDS = 120
DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_LLM_FINDINGS = 25

_TIMEOUT_HELP = "Whole-run budget in seconds."
_CHECK_TIMEOUT_HELP = "Per-subprocess timeout in seconds, capped by --timeout."
_PROVENANCE_HELP = "Write the JSON provenance record for this run to PATH."
_GENERATED_BY_HELP = "Who or what wrote this configuration. Recorded in provenance."
_REPORT_HELP = "Also write FORMAT=PATH. Repeatable, e.g. --report sarif=out.sarif."
_CONFIG_HELP = "Settings file. Defaults to PATH/.plimsoll/config.yaml when it exists."
_BASELINE_HELP = "Baseline file from `plimsoll baseline`; findings in it do not fail the run."
_STRICT_HELP = "Exit 3 unless every check ran. For CI that must not pass on a partial toolchain."

ProvenanceOption = Annotated[Path | None, typer.Option("--provenance", help=_PROVENANCE_HELP)]
GeneratedByOption = Annotated[str | None, typer.Option("--generated-by", help=_GENERATED_BY_HELP)]
ReportOption = Annotated[list[str] | None, typer.Option("--report", help=_REPORT_HELP)]
ConfigOption = Annotated[Path | None, typer.Option("--config", help=_CONFIG_HELP)]
BaselineOption = Annotated[Path | None, typer.Option("--baseline", help=_BASELINE_HELP)]
StrictOption = Annotated[bool | None, typer.Option("--strict/--no-strict", help=_STRICT_HELP)]
FailOnOption = Annotated[
    Severity | None,
    typer.Option(
        "--fail-on",
        help="Minimum severity that sets a non-zero exit. [default: HIGH]",
        case_sensitive=False,
    ),
]
PolicyDirOption = Annotated[
    Path | None, typer.Option("--policy-dir", help="Additional policy directory.")
]
TimeoutOption = Annotated[
    int | None,
    typer.Option("--timeout", help=f"{_TIMEOUT_HELP} [default: {DEFAULT_RUN_TIMEOUT_SECONDS}]"),
]
CheckTimeoutOption = Annotated[
    int | None,
    typer.Option(
        "--check-timeout",
        help=f"{_CHECK_TIMEOUT_HELP} [default: {DEFAULT_CHECK_TIMEOUT_SECONDS}]",
    ),
]
MaxLlmFindingsOption = Annotated[
    int | None,
    typer.Option(
        "--max-llm-findings",
        help=f"Cap on findings sent to the model. [default: {DEFAULT_MAX_LLM_FINDINGS}]",
    ),
]


class OutputFormat(StrEnum):
    TERMINAL = "terminal"
    JSON = "json"
    SARIF = "sarif"
    GITHUB = "github"
    JUNIT = "junit"


# `(result, *, fail_on) -> str` for every renderer. Spelled loosely because the two tables
# below take different result types and are otherwise identical.
Renderer = Callable[..., str]

# One table rather than a chain of `if`s in three commands. Adding a format is a row.
_GATE_RENDERERS: dict[OutputFormat, Renderer] = {
    OutputFormat.TERMINAL: render_terminal,
    OutputFormat.JSON: render_json,
    OutputFormat.SARIF: render_sarif,
    OutputFormat.GITHUB: render_github,
    OutputFormat.JUNIT: render_junit,
}

_VERIFY_RENDERERS: dict[OutputFormat, Renderer] = {
    OutputFormat.TERMINAL: render_verify_terminal,
    OutputFormat.JSON: render_verify_json,
    OutputFormat.SARIF: render_verify_sarif,
    OutputFormat.GITHUB: render_verify_github,
    OutputFormat.JUNIT: render_verify_junit,
}


def _parse_reports(specs: list[str] | None) -> list[tuple[OutputFormat, Path]]:
    """Parse `--report FORMAT=PATH` into pairs, or exit 2.

    A CI job wants three renderings of one run: SARIF for code scanning, markdown for the
    pull request, JSON for its own outputs. Without this it has to invoke the tool once
    per format, and the gate is a `terraform init`, a plan, and an `opa eval` -- so three
    formats cost three plans and can report three different results if anything in the
    workspace moved between them. They are renderings of one decision, and they are now
    produced from one.
    """
    parsed: list[tuple[OutputFormat, Path]] = []
    for spec in specs or []:
        name, separator, target = spec.partition("=")
        if not separator or not target:
            typer.echo(f"--report expects FORMAT=PATH, got {spec!r}", err=True)
            raise typer.Exit(ExitCode.CONFIG_ERROR)
        try:
            output_format = OutputFormat(name.strip().lower())
        except ValueError:
            allowed = ", ".join(f.value for f in OutputFormat)
            typer.echo(f"unknown report format {name!r}; expected one of: {allowed}", err=True)
            raise typer.Exit(ExitCode.CONFIG_ERROR) from None
        parsed.append((output_format, Path(target)))
    return parsed


def _write_reports(
    reports: list[tuple[OutputFormat, Path]],
    renderers: dict[OutputFormat, Renderer],
    result: GateResult | VerifyResult,
    fail_on: Severity,
) -> None:
    """Render each requested report to its path.

    A failure to write is reported and does not change the exit code, for the same reason
    provenance does not: the exit code describes the configuration that was judged, and a
    read-only mount is a fact about the runner.
    """
    for output_format, path in reports:
        body = renderers[output_format](result, fail_on=fail_on)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body + "\n", encoding="utf-8")
        except OSError as exc:
            typer.echo(f"could not write {output_format.value} to {path}: {exc}", err=True)
            continue
        typer.echo(f"{output_format.value} report written to {path}", err=True)


app = typer.Typer(
    name="plimsoll",
    help="Verification for AI-generated infrastructure code.",
    no_args_is_help=True,
    add_completion=False,
)


@dataclass(frozen=True)
class Settings:
    """What one run uses, after flags, `.plimsoll/config.yaml`, and defaults are merged.

    Resolved in one place so `scan`, `verify`, `explain`, and `baseline` cannot disagree
    about which source wins: a flag, then the file, then the built-in default.
    """

    fail_on: Severity
    timeout: int
    check_timeout: int
    policy_dir: Path | None
    baseline: Path | None
    strict: bool
    max_retries: int
    max_llm_findings: int

    def gate_config(self, baseline: frozenset[str] = frozenset()) -> GateConfig:
        return GateConfig(
            fail_on=self.fail_on,
            check=CheckConfig(timeout_seconds=self.check_timeout, policy_dir=self.policy_dir),
            baseline=baseline,
        )


T = TypeVar("T")


def _pick(flag: T | None, configured: T | None, default: T) -> T:
    if flag is not None:
        return flag
    return configured if configured is not None else default


def _settings(
    path: Path,
    *,
    config_file: Path | None = None,
    fail_on: Severity | None = None,
    timeout: int | None = None,
    check_timeout: int | None = None,
    policy_dir: Path | None = None,
    baseline: Path | None = None,
    strict: bool | None = None,
    max_retries: int | None = None,
    max_llm_findings: int | None = None,
) -> Settings:
    """Merge the sources and validate the result, or exit 2."""
    if not path.is_dir():
        typer.echo(f"not a directory: {path}", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)
    try:
        project = load_project_config(path, config_file)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR) from exc
    return _validated(
        Settings(
            fail_on=_pick(fail_on, project.fail_on, Severity.HIGH),
            timeout=_pick(timeout, project.timeout, DEFAULT_RUN_TIMEOUT_SECONDS),
            check_timeout=_pick(
                check_timeout, project.check_timeout, DEFAULT_CHECK_TIMEOUT_SECONDS
            ),
            policy_dir=policy_dir if policy_dir is not None else project.policy_dir,
            baseline=baseline if baseline is not None else project.baseline,
            strict=_pick(strict, project.strict, False),
            max_retries=_pick(max_retries, project.max_retries, DEFAULT_MAX_RETRIES),
            max_llm_findings=_pick(
                max_llm_findings, project.max_llm_findings, DEFAULT_MAX_LLM_FINDINGS
            ),
        )
    )


def _validated(settings: Settings) -> Settings:
    if settings.policy_dir is not None and not settings.policy_dir.is_dir():
        typer.echo(f"policy directory does not exist: {settings.policy_dir}", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)
    for flag, value in (
        ("--timeout", settings.timeout),
        ("--check-timeout", settings.check_timeout),
    ):
        if value <= 0:
            typer.echo(f"{flag} must be greater than zero", err=True)
            raise typer.Exit(ExitCode.CONFIG_ERROR)
    if settings.max_retries < 0:
        typer.echo("--max-retries cannot be negative", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)
    if settings.max_llm_findings <= 0:
        typer.echo("--max-llm-findings must be greater than zero", err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR)
    return settings


def _baseline_fingerprints(settings: Settings) -> frozenset[str]:
    """The fingerprints to suppress, or exit 2 when a named baseline cannot be read."""
    if settings.baseline is None:
        return frozenset()
    try:
        return load_baseline(settings.baseline).fingerprints
    except BaselineError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(ExitCode.CONFIG_ERROR) from exc


def _incomplete(result: GateResult) -> list[str]:
    """Checks that did not run to completion, for --strict."""
    return [s.name for s in result.checks_skipped] + [f.name for f in result.checks_failed]


def _strict_failure(result: GateResult, strict: bool) -> bool:
    incomplete = _incomplete(result)
    if strict and incomplete:
        typer.echo(
            f"--strict: {len(incomplete)} check(s) did not run ({', '.join(incomplete)}), "
            "so this run is not a full verification.",
            err=True,
        )
        return True
    return False


def _exit_code(result: GateResult, fail_on: Severity, strict: bool = False) -> int:
    # Order matters, and timing out comes first: it is the reason the rest of the run is
    # incomplete, so reporting anything else would bury the cause. A run where nothing
    # could be verified reports that next, even when it collected no findings --
    # especially then. A broken toolchain must not masquerade as a clean pass, and
    # neither may a truncated one. Under --strict, a partial toolchain is the same.
    if result.timed_out:
        return ExitCode.TIMED_OUT
    if not result.any_check_ran:
        return ExitCode.NOTHING_RAN
    if _strict_failure(result, strict):
        return ExitCode.NOTHING_RAN
    if result.findings_at_or_above(fail_on):
        return ExitCode.FINDINGS
    return ExitCode.CLEAN


def _verify_exit_code(result: VerifyResult, fail_on: Severity, strict: bool = False) -> int:
    """Like `_exit_code`, but a verified fix clears the finding it fixed.

    Derived from `Decision.status`, which only `VerifierLoop` sets and which cannot be
    `FIXED` without a `PatchVerification` the gate produced.
    """
    if result.timed_out:
        return ExitCode.TIMED_OUT
    if not result.gate.any_check_ran:
        return ExitCode.NOTHING_RAN
    if _strict_failure(result.gate, strict):
        return ExitCode.NOTHING_RAN
    if result.unresolved_at_or_above:
        return ExitCode.FINDINGS
    return ExitCode.CLEAN


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


def _write_provenance(
    result: VerifyResult,
    path: Path,
    workspace: Path,
    *,
    model: ModelIdentity | None,
    generated_by: str | None,
) -> None:
    """Emit the record, or say why not.

    A failure to write is reported and does not change the exit code. The exit code
    describes the configuration that was judged; a full disk is a fact about the runner.
    """
    try:
        written = write_record(
            build_record(result, workspace, model=model, generated_by=generated_by), path
        )
    except OSError as exc:
        typer.echo(f"could not write the provenance record to {path}: {exc}", err=True)
        return
    typer.echo(f"Provenance written to {written}", err=True)


def _model_identity(provider: LLMProvider | None) -> ModelIdentity | None:
    """Which model was consulted, for the record. Never carries the API key."""
    if provider is None:
        return None
    config = LLMConfig.from_env()
    return ModelIdentity(
        provider=config.provider,
        model=config.model,
        temperature=config.temperature,
        max_retries=config.max_retries,
    )


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
    fail_on: FailOnOption = None,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", help="Report format.")
    ] = OutputFormat.TERMINAL,
    policy_dir: PolicyDirOption = None,
    timeout: TimeoutOption = None,
    check_timeout: CheckTimeoutOption = None,
    provenance: ProvenanceOption = None,
    generated_by: GeneratedByOption = None,
    report: ReportOption = None,
    config: ConfigOption = None,
    baseline: BaselineOption = None,
    strict: StrictOption = None,
) -> None:
    """Run the deterministic gate. No model is involved."""
    settings = _settings(
        path,
        config_file=config,
        fail_on=fail_on,
        timeout=timeout,
        check_timeout=check_timeout,
        policy_dir=policy_dir,
        baseline=baseline,
        strict=strict,
    )
    reports = _parse_reports(report)
    exceptions = _load_exceptions(path)

    gate_config = settings.gate_config(_baseline_fingerprints(settings))
    result = default_gate().run(path, gate_config, Deadline(settings.timeout))
    for lapsed in suppress_exempted(result, exceptions):
        when = f" on {lapsed.expires.isoformat()}" if lapsed.expires else ""
        typer.echo(
            f"exception for {lapsed.rule_class} on {lapsed.resource_address} expired{when}; "
            f"{lapsed.approved_by}'s sign-off no longer applies and the finding counts again.",
            err=True,
        )
    _write_reports(reports, _GATE_RENDERERS, result, settings.fail_on)

    if provenance is not None:
        _write_provenance(
            gate_as_verify_result(result, settings.fail_on),
            provenance,
            path,
            model=None,
            generated_by=generated_by,
        )

    typer.echo(_GATE_RENDERERS[output_format](result, fail_on=settings.fail_on))
    raise typer.Exit(_exit_code(result, settings.fail_on, settings.strict))


def _run_loop(
    path: Path,
    settings: Settings,
    output_format: OutputFormat,
    no_llm: bool,
    explain_only: bool,
    provenance: Path | None = None,
    generated_by: str | None = None,
    report: list[str] | None = None,
) -> None:
    reports = _parse_reports(report)
    gate_config = settings.gate_config(_baseline_fingerprints(settings))
    provider, reason = _resolve_provider(no_llm)
    gate: Gate = default_gate()

    loop = VerifierLoop(
        gate,
        gate_config,
        provider,
        max_retries=settings.max_retries,
        max_findings=settings.max_llm_findings,
        exceptions=_load_exceptions(path),
    )
    result = loop.run(path, explain_only=explain_only, deadline=Deadline(settings.timeout))
    if provider is None and reason:
        result.llm_unavailable_reason = reason

    # After `llm_unavailable_reason` is set: a report that omitted "no model was used"
    # would read as a run where the model had nothing to fix.
    _write_reports(reports, _VERIFY_RENDERERS, result, settings.fail_on)

    if provenance is not None:
        _write_provenance(
            result,
            provenance,
            path,
            model=_model_identity(provider),
            generated_by=generated_by,
        )

    typer.echo(_VERIFY_RENDERERS[output_format](result, fail_on=settings.fail_on))
    raise typer.Exit(_verify_exit_code(result, settings.fail_on, settings.strict))


@app.command()
def verify(
    path: Annotated[Path, typer.Argument(help="Directory of Terraform to verify.")],
    fail_on: FailOnOption = None,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", help="Report format.")
    ] = OutputFormat.TERMINAL,
    policy_dir: PolicyDirOption = None,
    timeout: TimeoutOption = None,
    check_timeout: CheckTimeoutOption = None,
    no_llm: Annotated[
        bool, typer.Option("--no-llm", help="Deterministic mode. Same as scan, with decisions.")
    ] = False,
    max_retries: Annotated[
        int | None,
        typer.Option(
            "--max-retries",
            help=f"Patch attempts per finding before escalation. [default: {DEFAULT_MAX_RETRIES}]",
        ),
    ] = None,
    max_llm_findings: MaxLlmFindingsOption = None,
    provenance: ProvenanceOption = None,
    generated_by: GeneratedByOption = None,
    report: ReportOption = None,
    config: ConfigOption = None,
    baseline: BaselineOption = None,
    strict: StrictOption = None,
) -> None:
    """Scan, draft fixes, re-verify them through the same gate, escalate what fails.

    The workspace is never modified: patches are applied to a temporary copy. Only
    fixes that pass re-verification are shown.
    """
    settings = _settings(
        path,
        config_file=config,
        fail_on=fail_on,
        timeout=timeout,
        check_timeout=check_timeout,
        policy_dir=policy_dir,
        baseline=baseline,
        strict=strict,
        max_retries=max_retries,
        max_llm_findings=max_llm_findings,
    )
    _run_loop(
        path,
        settings,
        output_format,
        no_llm,
        explain_only=False,
        provenance=provenance,
        generated_by=generated_by,
        report=report,
    )


@app.command()
def explain(
    path: Annotated[Path, typer.Argument(help="Directory of Terraform to explain.")],
    fail_on: FailOnOption = None,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", help="Report format.")
    ] = OutputFormat.TERMINAL,
    policy_dir: PolicyDirOption = None,
    timeout: TimeoutOption = None,
    check_timeout: CheckTimeoutOption = None,
    max_llm_findings: MaxLlmFindingsOption = None,
    provenance: ProvenanceOption = None,
    generated_by: GeneratedByOption = None,
    report: ReportOption = None,
    config: ConfigOption = None,
    baseline: BaselineOption = None,
    strict: StrictOption = None,
) -> None:
    """Scan and explain the findings in prose. No patches are drafted."""
    settings = _settings(
        path,
        config_file=config,
        fail_on=fail_on,
        timeout=timeout,
        check_timeout=check_timeout,
        policy_dir=policy_dir,
        baseline=baseline,
        strict=strict,
        max_retries=0,
        max_llm_findings=max_llm_findings,
    )
    _run_loop(
        path,
        settings,
        output_format,
        no_llm=False,
        explain_only=True,
        provenance=provenance,
        generated_by=generated_by,
        report=report,
    )


@app.command("baseline")
def baseline_command(
    path: Annotated[Path, typer.Argument(help="Directory of Terraform to snapshot.")],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help=f"Where to write it. [default: PATH/{DEFAULT_BASELINE_PATH.as_posix()}]",
        ),
    ] = None,
    policy_dir: PolicyDirOption = None,
    timeout: TimeoutOption = None,
    check_timeout: CheckTimeoutOption = None,
    config: ConfigOption = None,
) -> None:
    """Record today's findings so later runs fail only on new ones.

    For adopting the gate on a repository that already has violations. Commit the file;
    a pull request that grows it shows up in review. Exceptions, not the baseline, are
    for violations someone has actually accepted.
    """
    settings = _settings(
        path,
        config_file=config,
        timeout=timeout,
        check_timeout=check_timeout,
        policy_dir=policy_dir,
    )
    # Never filtered by an existing baseline: re-recording must see everything.
    result = default_gate().run(path, settings.gate_config(), Deadline(settings.timeout))
    typer.echo(render_terminal(result, fail_on=settings.fail_on), err=True)
    if result.timed_out:
        typer.echo("Not written: the run timed out, so the snapshot would be partial.", err=True)
        raise typer.Exit(ExitCode.TIMED_OUT)
    if not result.any_check_ran:
        typer.echo("Not written: no check ran, so there is nothing to record.", err=True)
        raise typer.Exit(ExitCode.NOTHING_RAN)

    target = output or settings.baseline or path / DEFAULT_BASELINE_PATH
    snapshot = Baseline.from_findings(result.findings)
    write_baseline(snapshot, target)
    incomplete = _incomplete(result)
    if incomplete:
        # Written anyway: a baseline missing a check's findings suppresses less, never
        # more. But whoever commits it should know it will need re-recording.
        typer.echo(
            f"warning: {', '.join(incomplete)} did not run; findings from it are not in "
            "the baseline and will fail later runs that have it.",
            err=True,
        )
    typer.echo(f"Baseline of {len(snapshot.findings)} finding(s) written to {target}.")


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
