"""Publish all companies that have a decision.md to the configured Google
Docs. Skips memo regen; uses the AngelList metadata cache when present.

Run after rotating doc structure or after a batch of decisions."""

import os
import subprocess
import sys
from pathlib import Path

COMPANIES = [
    "CarbonCrusher",
    "Emulate Cities",
    "Exowatt",
    "HiCap",
    "Leap",
    "Mitti",
    "OneNav",
    "Pyka",
    "Quaise",
]


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    logs_dir = repo_root / ".tmp" / "publish_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    results: list[tuple[str, int]] = []
    for i, company in enumerate(COMPANIES, start=1):
        log_path = logs_dir / f"{company}.log"
        print(f"[{i}/{len(COMPANIES)}] {company}", flush=True)
        with log_path.open("w", encoding="utf-8") as f:
            result = subprocess.run(
                [sys.executable, "-m", "angel_memos.cli", "publish", company],
                stdout=f,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=str(repo_root),
            )
        results.append((company, result.returncode))
        status = "OK" if result.returncode == 0 else f"FAIL ({result.returncode})"
        print(f"  -> {status}", flush=True)

    print("\n=== Summary ===")
    for co, rc in results:
        status = "OK" if rc == 0 else "FAIL"
        print(f"  {status:5} {co}")
    return 1 if any(rc != 0 for _, rc in results) else 0


if __name__ == "__main__":
    sys.exit(main())
