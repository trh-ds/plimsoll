"""Regenerate the published provenance schema.

    uv run python -m plimsoll.core.schema

The committed `schemas/provenance-0.1.json` is what anyone parsing a record reads, and
`tests/test_provenance.py` fails when it drifts from the model. This script is how you
make it stop failing, deliberately, in the same commit as the model change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from plimsoll.core.provenance import SCHEMA_VERSION, ProvenanceRecord

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "schemas" / f"provenance-{SCHEMA_VERSION}.json"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    path = Path(args[0]) if args else DEFAULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = ProvenanceRecord.model_json_schema()
    path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
