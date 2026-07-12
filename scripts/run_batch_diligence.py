"""Drive `angel-memos diligence` across every company in the Evaluation
folder. Sequential (one Claude session at a time to avoid rate limits) with
per-company log files so any failure is contained."""

import os
import subprocess
import sys
from pathlib import Path

COMPANIES = [
    "CarbonCrusher",
    "Emulate Cities",
    "Exowatt",
    "Leap",
    "Mitti",
    "OneNav",
    "Pyka",
    "Quaise",
    "Revoy",
    "Volatis",
]


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    logs_dir = repo_root / ".tmp" / "diligence_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)  # force subscription billing

    results: list[tuple[str, int, Path]] = []
    for i, company in enumerate(COMPANIES, start=1):
        safe = company.replace(" ", "_")
        log_path = logs_dir / f"{safe}.log"
        print(f"[{i}/{len(COMPANIES)}] {company} -> {log_path}", flush=True)
        with log_path.open("w", encoding="utf-8") as f:
            result = subprocess.run(
                [sys.executable, "-m", "angel_memos.cli", "diligence", company],
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
    failures = sum(1 for _, rc, _ in results if rc != 0)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
