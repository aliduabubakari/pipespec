from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FILES = [
    (
        ROOT / "schema" / "pipespec_schema_v1.json",
        ROOT / "src" / "pipespec_validator" / "data" / "pipespec_schema_v1.json",
    ),
    (
        ROOT / "schema" / "pipespec_prompt_profile_v1.json",
        ROOT / "src" / "pipespec_validator" / "data" / "pipespec_prompt_profile_v1.json",
    ),
]


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    for src, dst in FILES:
        if not src.exists():
            raise SystemExit(f"Missing source file: {src}")

        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        a = _sha256(src)
        b = _sha256(dst)
        if a != b:
            raise SystemExit(f"Sync failed: checksums differ for:\n  {src}\n  {dst}")

        print(f"Synced:\n  {src}\n  -> {dst}")


if __name__ == "__main__":
    main()