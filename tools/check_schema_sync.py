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
            raise SystemExit(f"Missing: {src}")
        if not dst.exists():
            raise SystemExit(f"Missing: {dst} (run: python tools/sync_schema_into_package.py)")

        a = _sha256(src)
        b = _sha256(dst)
        if a != b:
            raise SystemExit(
                "Bundled data is out of sync.\n"
                f"  {src}\n"
                f"  {dst}\n"
                "Run: python tools/sync_schema_into_package.py"
            )

    print("Schema + prompt profile sync OK.")


if __name__ == "__main__":
    main()