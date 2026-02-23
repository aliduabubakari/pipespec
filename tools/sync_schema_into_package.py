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
        raise SystemExit(f"Missing schema file: {SRC}")
    if not DST.parent.exists():
        DST.parent.mkdir(parents=True, exist_ok=True)

    DST.write_text(SRC.read_text(encoding="utf-8"), encoding="utf-8")

    a = _sha256(SRC)
    b = _sha256(DST)
    if a != b:
        raise SystemExit("Schema sync failed: checksums differ")
    print(f"Synced schema into package:\n  {SRC}\n  -> {DST}")


if __name__ == "__main__":
    main()