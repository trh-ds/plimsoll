"""Post the rendered report to the pull request, editing last run's comment in place.

Not part of the package. This is glue that belongs to the Action, it runs only inside the
Action's image, and it depends on nothing outside the standard library so the image does
not grow an HTTP client to make one request.

Editing rather than appending is the whole point. A bot that adds a fresh comment on
every push turns a ten-commit pull request into ten copies of the same report, and the
reviewer mutes the bot rather than reading the eleventh. `reporters.github.MARKER` is an
HTML comment carried in the report's footer, which is how a previous comment is found.

Failure here is never fatal to the job. Whether the configuration is compliant was
decided by the gate; whether GitHub accepted a comment is a fact about GitHub.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

MARKER = "<!-- plimsoll:report -->"
API = os.environ.get("GITHUB_API_URL", "https://api.github.com")

# GitHub rejects a comment body over 65536 characters outright, which on a large
# configuration would mean no comment at all rather than a long one.
MAX_BODY = 65_000


def request(
    method: str, url: str, token: str, payload: dict[str, str] | None = None
) -> list[dict[str, object]]:
    """One request. Returns the decoded body as a list, so the caller has one shape.

    A comment listing is a JSON array and a created comment is a JSON object; wrapping
    the object saves every caller an isinstance check for a difference that means nothing
    here.
    """
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "plimsoll-action")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as response:
        decoded = json.load(response)
    return decoded if isinstance(decoded, list) else [decoded]


def pull_request_number(event_path: str | None) -> int | None:
    """The PR this run belongs to, from the event payload. None when it is not a PR."""
    if not event_path or not os.path.isfile(event_path):
        return None
    with open(event_path, encoding="utf-8") as handle:
        event = json.load(handle)
    if not isinstance(event, dict):
        return None
    for key in ("pull_request", "issue"):
        number = (event.get(key) or {}).get("number")
        if isinstance(number, int):
            return number
    return None


def truncate(body: str) -> str:
    if len(body) <= MAX_BODY:
        return body
    notice = (
        "\n\n> Report truncated to fit GitHub's comment limit."
        " The full report is in this run's artifacts.\n"
    )
    return body[: MAX_BODY - len(notice) - len(MARKER)] + notice + MARKER


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN") or ""
    repository = os.environ.get("GITHUB_REPOSITORY") or ""
    path = sys.argv[1] if len(sys.argv) > 1 else ""

    if not token:
        print("::warning::pr-comment is on but no GITHUB_TOKEN was passed to the step.")
        return 0
    if not repository or not os.path.isfile(path):
        print("::warning::no report to post.")
        return 0

    number = pull_request_number(os.environ.get("GITHUB_EVENT_PATH"))
    if number is None:
        print("This run is not attached to a pull request; skipping the comment.")
        return 0

    with open(path, encoding="utf-8") as handle:
        body = truncate(handle.read())

    base = f"{API}/repos/{repository}/issues"
    try:
        existing = request("GET", f"{base}/{number}/comments?per_page=100", token)
        mine = next((c for c in existing if MARKER in (c.get("body") or "")), None)
        if mine:
            request("PATCH", f"{base}/comments/{mine['id']}", token, {"body": body})
            print(f"Updated comment {mine['id']} on #{number}.")
        else:
            request("POST", f"{base}/{number}/comments", token, {"body": body})
            print(f"Commented on #{number}.")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # Most often a token without `pull-requests: write`. Worth saying plainly, and
        # not worth failing a job whose verdict was already decided by the gate.
        print(f"::warning::could not post the pull request comment: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
