#!/usr/bin/env bash
#
# The Action's entrypoint. One Plimsoll run, every report the caller asked for, the
# counts published as step outputs, and the CLI's own exit code passed through.
#
# Three things it deliberately does not do:
#
#   It does not run the gate more than once. A `terraform init`, a plan, and an `opa
#   eval` is minutes, and SARIF, markdown, and JSON are three renderings of one decision
#   rather than three runs -- `--report FORMAT=PATH` exists for exactly this.
#
#   It does not translate exit codes. The contract is 0 clean, 1 findings, 2 config
#   error, 3 nothing ran, 4 timed out. A wrapper that collapsed 3 into 0 would let a run
#   where no adapter could start report success. `fail-on-findings: false` suppresses
#   exit 1 and nothing else, and says so in the log.
#
#   It does not decide anything. Every number below is read out of the JSON report the
#   gate produced.
set -uo pipefail

fail() {
    echo "::error::$*"
    exit 2
}

command_name="${INPUT_COMMAND:-scan}"
case "$command_name" in
    scan | verify | explain) ;;
    *) fail "command must be scan, verify, or explain; got '${command_name}'" ;;
esac

workspace="${INPUT_PATH:-.}"
[ -d "$workspace" ] || fail "path does not exist in the checkout: ${workspace}"

reports="${INPUT_REPORT_DIR:-plimsoll-reports}"
mkdir -p "$reports" || fail "could not create the report directory: ${reports}"

json_report="${reports}/plimsoll.json"
sarif_report="${reports}/plimsoll.sarif"
comment_report="${reports}/plimsoll-comment.md"
provenance_report="${reports}/provenance.json"

args=("$command_name" "$workspace" "--fail-on" "${INPUT_FAIL_ON:-high}")
args+=("--report" "json=${json_report}")

[ -n "${INPUT_POLICY_DIR:-}" ] && args+=("--policy-dir" "${INPUT_POLICY_DIR}")
[ -n "${INPUT_TIMEOUT:-}" ] && args+=("--timeout" "${INPUT_TIMEOUT}")
[ -n "${INPUT_CHECK_TIMEOUT:-}" ] && args+=("--check-timeout" "${INPUT_CHECK_TIMEOUT}")
[ -n "${INPUT_GENERATED_BY:-}" ] && args+=("--generated-by" "${INPUT_GENERATED_BY}")

[ "${INPUT_SARIF:-true}" = "true" ] && args+=("--report" "sarif=${sarif_report}")
[ "${INPUT_COMMENT:-true}" = "true" ] && args+=("--report" "github=${comment_report}")
[ "${INPUT_PROVENANCE:-true}" = "true" ] && args+=("--provenance" "$provenance_report")

if [ "$command_name" = "verify" ]; then
    [ "${INPUT_NO_LLM:-false}" = "true" ] && args+=("--no-llm")
    [ -n "${INPUT_MAX_RETRIES:-}" ] && args+=("--max-retries" "${INPUT_MAX_RETRIES}")
fi

# `--format terminal` on stdout, so the step log is readable without downloading an
# artifact. The files above are written by the same run.
echo "::group::plimsoll ${args[*]}"
plimsoll "${args[@]}"
status=$?
echo "::endgroup::"

[ -s "$json_report" ] || fail "plimsoll produced no JSON report; its exit code was ${status}"

# Read together, so the report is parsed once and no two outputs can disagree.
mapfile -t counts < <(python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    summary = json.load(handle)["summary"]
for key in ("findings", "blocking", "fixed", "escalated", "exempted"):
    print(summary.get(key, 0))
' "$json_report")

{
    echo "findings=${counts[0]:-0}"
    echo "blocking=${counts[1]:-0}"
    echo "fixed=${counts[2]:-0}"
    echo "escalated=${counts[3]:-0}"
    echo "exempted=${counts[4]:-0}"
    echo "exit-code=${status}"
    echo "report-dir=${reports}"
    echo "json-report=${json_report}"
    [ -s "$sarif_report" ] && echo "sarif-report=${sarif_report}"
    [ -s "$comment_report" ] && echo "comment-report=${comment_report}"
    [ -s "$provenance_report" ] && echo "provenance=${provenance_report}"
} >> "${GITHUB_OUTPUT:-/dev/null}"

# The job summary, so a reader sees the report without opening the artifact or the log.
[ -s "$comment_report" ] && cat "$comment_report" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"

if [ "${INPUT_PR_COMMENT:-false}" = "true" ] && [ -s "$comment_report" ]; then
    python3 /usr/local/bin/post_comment.py "$comment_report"
fi

case "$status" in
    0) ;;
    1)
        if [ "${INPUT_FAIL_ON_FINDINGS:-true}" != "true" ]; then
            echo "::warning::${counts[1]:-0} finding(s) at or above ${INPUT_FAIL_ON:-high}." \
                 "Not failing the job, because fail-on-findings is false."
            exit 0
        fi
        echo "::error::${counts[1]:-0} finding(s) at or above ${INPUT_FAIL_ON:-high}."
        ;;
    2) echo "::error::plimsoll could not start. Check the inputs to this step." ;;
    3) echo "::error::no check could run, so nothing was verified. This is not a pass." ;;
    4) echo "::error::the run exceeded its timeout. Partial verification is not verification." ;;
esac

exit "$status"
