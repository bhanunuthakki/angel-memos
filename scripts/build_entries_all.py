"""Generate private_entry.json (and for buys, public_entry.json +
memo_public.md) for every company that has a decision.md.

Runs `angel-memos memo --skip-long-memo --no-docs --no-review` per company,
which:
  - Reuses existing memo_private.md (skips long-form Claude call)
  - Reuses cached AL metadata if present
  - Generates the structured private entry via Claude
  - For buys, also parses deck (cached) + generates public entry + writes
    memo_public.md
  - Does NOT push to docs (run scripts/publish_all.py separately)

Write a log per company under .tmp/build_entries_logs/.
"""

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
    logs_dir = repo_root / ".tmp" / "build_entries_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    results: list[tuple[str, int]] = []
    for i, company in enumerate(COMPANIES, start=1):
        log_path = logs_dir / f"{company}.log"
        print(f"[{i}/{len(COMPANIES)}] {company}", flush=True)
        with log_path.open("w", encoding="utf-8") as f:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "angel_memos.cli",
                    "memo",
                    company,
                    "--skip-long-memo",
                    "--no-docs",
                    "--no-review",
                ],
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
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
