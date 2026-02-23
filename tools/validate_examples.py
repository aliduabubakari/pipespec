from __future__ import annotations

from pathlib import Path

from pipespec_validator.validator import validate_file


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "schema" / "examples"


def main() -> None:
    if not EXAMPLES.exists():
        raise SystemExit(f"Missing examples dir: {EXAMPLES}")

    files = sorted([p for p in EXAMPLES.iterdir() if p.is_file() and p.suffix.lower() in {".json", ".yaml", ".yml"}])
    if not files:
        raise SystemExit("No example files found.")

    failures = 0
    for f in files:
        res = validate_file(f, semantic_checks=True)
        if not res.ok:
            failures += 1
            print(f"FAIL: {f}")
            for e in res.errors:
                print(f"  - {e.kind} {e.instance_path}: {e.message}")
        else:
            print(f"OK:   {f}")

    if failures:
        raise SystemExit(f"{failures} example(s) failed validation.")
    print("All examples validated successfully.")


if __name__ == "__main__":
    main()