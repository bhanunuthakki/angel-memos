"""One-off: append the existing HiCap memo files to the configured Google
Docs. Re-uses the already-generated memo_private.md / memo_public.md from
the prior `angel-memos memo HiCap --no-docs` run, so it doesn't burn a new
Claude call — purely tests the OAuth + doc-append path.

First run triggers the browser OAuth flow; token caches to
`<config_dir>/google-token.json` for subsequent runs.
"""

from datetime import date
from pathlib import Path

from angel_memos.config import load_config
from angel_memos.google_docs import append_to_top
from angel_memos.memo import parse_decision


def main() -> None:
    folder = Path(r"G:\My Drive\Personal Finances\Angel Investing\Evaluation\HiCap")
    decision = parse_decision(folder / "decision.md")
    cfg = load_config()
    private_md = (folder / "memo_private.md").read_text(encoding="utf-8")
    public_md = (folder / "memo_public.md").read_text(encoding="utf-8")
    heading = f"{decision.company} - {date.today().isoformat()}"

    print(f"Appending to private doc ({cfg.private_doc_id})...")
    append_to_top(cfg.private_doc_id, heading, private_md)
    print("  ok")

    print(f"Appending to public doc ({cfg.public_doc_id})...")
    append_to_top(cfg.public_doc_id, heading, public_md)
    print("  ok")


if __name__ == "__main__":
    main()
