"""Drive `angel-memos memo` across the three pass-decision companies.
Sequential to avoid rate limits; per-company log files for triage."""

import os
import subprocess
import sys
from pathlib import Path

COMPANIES = [
    "CarbonCrusher",
    "Emulate Cities",
    "Exowatt",
    "OneNav",
    "Pyka",
]


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    logs_dir = repo_root / ".tmp" / "memo_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    results: list[tuple[str, int, Path]] = []
    for i, company in enumerate(COMPANIES, start=1):
        log_path = logs_dir / f"{company}.log"
        print(f"[{i}/{len(COMPANIES)}] {company} -> {log_path}", flush=True)
        with log_path.open("w", encoding="utf-8") as f:
            result = subprocess.run(
                [sys.executable, "-m", "angel_memos.cli", "memo", company],
                stdout=f,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=str(repo_root),
            )
        results.append((company, result.returncode, log_path))
        status = "OK" if result.returncode == 0 else f"FAIL (exit={result.returncode})"
        print(f"  -> {status}", flush=True)

    print("\n=== Summary ===")
    for company, rc, log in results:
        status = "OK" if rc == 0 else "FAIL"
        print(f"  {status:5} {company:20} ({log})")
    return 1 if any(rc != 0 for _, rc, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
