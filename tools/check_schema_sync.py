from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "schema" / "pipespec_schema_v1.json"
DST = ROOT / "src" / "pipespec_validator" / "data" / "pipespec_schema_v1.json"


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"Missing: {SRC}")
    if not DST.exists():
        raise SystemExit(f"Missing: {DST} (run: python tools/sync_schema_into_package.py)")

    a = _sha256(SRC)
    b = _sha256(DST)
    if a != b:
        raise SystemExit(
            "Bundled schema is out of sync.\n"
            f"  {SRC}\n"
            f"  {DST}\n"
            "Run: python tools/sync_schema_into_package.py"
        )

    print("Schema sync OK.")


if __name__ == "__main__":
    main()