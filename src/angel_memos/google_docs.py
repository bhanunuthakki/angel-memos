"""Google Docs writer for the per-deal investment log.

Two docs, two distinct entry shapes:

  PRIVATE  (the comprehensive working ledger — every decision):
    H2 "Portfolio"
      H3 <Company>
        H4 "Rationale"      bullet × N
        H4 "Risks"          bullet × N
    H2 "Passed Deals"
      H3 [Passed] <Company>
        H4 "Rationale"      bullet × N
        H4 "Why Passing?"   bullet × N

  PUBLIC   (shareable, anonymized, BUYS ONLY):
    H3 "Memos"
      H4 <Category Descriptor> — <Stage> Deal Memo
        Date: <Month Year>
        **What does it do?** <paragraph>
        **Why is it important?** <paragraph>
        **Market & Opportunity**
          * Job(s) to be done: ...
          * Market Size: ...
          * Why Now?: ...
        **Team**
          * Founder Market Fit: ...
          * Superpower or Execution Advantage: ...
        **Key Metrics (Anonymized Ranges)**
          * ARR / Contracted Revenue: ...
          * Retention: ...
          * Efficiency / Techno-Economics: ...
        **Competitive Moat & Company Superpower** (optional)
          * The Structural Advantage: ...
          * Execution Velocity: ...
        **Anti-Thesis**
          * <full paragraph 1>
          * <full paragraph 2>
          * ...
        **Bull Case**
          * Thesis: ...
          * Verdict: GO. ...

Both shapes share one primitive: `insert_blocks_under(doc_id, container,
level, blocks)` finds the named container heading and inserts a list of
typed `Block`s immediately after it (newest-first within each container).

OAuth desktop flow on first use; refresh token cached at
`<config_dir>/google-token.json`. Requires `credentials.json` at
`<config_dir>/credentials.json` (one-time setup).
"""

# The googleapiclient discovery API is dynamically typed; its partial stubs
# leak unknowns through every chained call. Contain that here rather than
# annotating each hop.
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from angel_memos.config import config_dir
from angel_memos.doc_entries import (
    PrivateDocEntry,
    PublicDocEntry,
    private_company_heading,
    private_second_section_label,
    public_entry_heading,
)
from angel_memos.models import Decision

_SCOPES = ["https://www.googleapis.com/auth/documents"]
_CREDENTIALS_FILENAME = "credentials.json"
_TOKEN_FILENAME = "google-token.json"

_PRIVATE_CONTAINER_LEVEL = 2
_PRIVATE_COMPANY_LEVEL = 3
_PRIVATE_SUBSECTION_LEVEL = 4

_PUBLIC_CONTAINER_LEVEL = 3
_PUBLIC_COMPANY_LEVEL = 4

BlockStyle = Literal[
    "heading_2",
    "heading_3",
    "heading_4",
    "heading_5",
    "normal",
    "bullet",
]


@dataclass(frozen=True)
class Block:
    """One paragraph-level block of content to insert into a doc.

    `text` is the visible paragraph text (no trailing newline — the composer
    appends it). `style` controls the paragraph style. `bold_prefix_chars`
    bolds the first N characters of the visible text, used for bold-inline
    labels like 'What does it do? <body>' or section banners like
    '**Market & Opportunity**' (where the entire text is bolded).
    """

    text: str
    style: BlockStyle
    bold_prefix_chars: int = 0


class GoogleDocsAuthError(RuntimeError):
    """Raised when OAuth setup is missing or invalid."""


class GoogleDocsStructureError(RuntimeError):
    """Raised when the target document doesn't have the expected heading."""


# ---------------------------------------------------------------------------
# Public API: high-level entry inserters.
# ---------------------------------------------------------------------------


def insert_private_entry(
    doc_id: str,
    container_heading: str,
    decision: Decision,
    entry: PrivateDocEntry,
) -> None:
    """Insert a private-doc entry under the named container (Portfolio or
    Passed Deals). The company heading gets the `[Passed]` prefix for
    non-buys; the second section is labeled "Risks" or "Why Passing?".
    """
    blocks = _build_private_blocks(decision, entry)
    insert_blocks_under(doc_id, container_heading, _PRIVATE_CONTAINER_LEVEL, blocks)


def insert_public_entry(
    doc_id: str,
    container_heading: str,
    entry: PublicDocEntry,
) -> None:
    """Insert a public-doc entry under the named container (Memos).

    Used only for buy/strong_buy decisions; pass entries don't reach the
    public doc."""
    blocks = _build_public_blocks(entry)
    insert_blocks_under(doc_id, container_heading, _PUBLIC_CONTAINER_LEVEL, blocks)


def insert_blocks_under(
    doc_id: str,
    container_heading: str,
    container_level: int,
    blocks: list[Block],
) -> None:
    """Insert `blocks` immediately after the named container heading.

    Raises:
      GoogleDocsStructureError: the doc has no heading paragraph matching
        the requested `container_heading` at the expected `container_level`.
    """
    service = build("docs", "v1", credentials=_load_credentials())
    doc = service.documents().get(documentId=doc_id).execute()
    insert_at = _find_container_heading_end(doc, container_heading, container_level)

    text, requests_meta = _compose_blocks(blocks)
    api_requests: list[dict[str, Any]] = [
        {"insertText": {"location": {"index": insert_at}, "text": text}},
    ]
    for named_style, rel_start, rel_end in requests_meta["paragraph_styles"]:
        api_requests.append(
            {
                "updateParagraphStyle": {
                    "range": {
                        "startIndex": insert_at + rel_start,
                        "endIndex": insert_at + rel_end,
                    },
                    "paragraphStyle": {"namedStyleType": named_style},
                    "fields": "namedStyleType",
                }
            }
        )
    for rel_start, rel_end in requests_meta["bold_ranges"]:
        api_requests.append(
            {
                "updateTextStyle": {
                    "range": {
                        "startIndex": insert_at + rel_start,
                        "endIndex": insert_at + rel_end,
                    },
                    "textStyle": {"bold": True},
                    "fields": "bold",
                }
            }
        )
    for rel_start, rel_end in requests_meta["bullet_ranges"]:
        api_requests.append(
            {
                "createParagraphBullets": {
                    "range": {
                        "startIndex": insert_at + rel_start,
                        "endIndex": insert_at + rel_end,
                    },
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                }
            }
        )

    service.documents().batchUpdate(documentId=doc_id, body={"requests": api_requests}).execute()


# ---------------------------------------------------------------------------
# Block builders — pure functions; unit-testable.
# ---------------------------------------------------------------------------


def _build_private_blocks(decision: Decision, entry: PrivateDocEntry) -> list[Block]:
    """Compose the block list for one private-doc entry."""
    company_heading = private_company_heading(decision)
    second_label = private_second_section_label(decision)
    blocks: list[Block] = [
        Block(text=company_heading, style="heading_3"),
        Block(text="Rationale", style="heading_4"),
    ]
    blocks.extend(Block(text=b, style="bullet") for b in entry.rationale_bullets)
    blocks.append(Block(text=second_label, style="heading_4"))
    blocks.extend(Block(text=b, style="bullet") for b in entry.second_section_bullets)
    return blocks


def _build_public_blocks(entry: PublicDocEntry) -> list[Block]:
    """Compose the block list for one public-doc entry per the style guide."""
    blocks: list[Block] = [
        Block(text=public_entry_heading(entry), style="heading_4"),
        Block(text=f"Date: {entry.date_label}", style="normal"),
    ]
    blocks.extend(_paragraph_with_bold_prefix("What does it do?", entry.what_does_it_do))
    blocks.extend(_paragraph_with_bold_prefix("Why is it important?", entry.why_is_it_important))

    blocks.extend(
        _section_block_with_bullets(
            "Market & Opportunity",
            [
                ("Job(s) to be done:", entry.market_and_opportunity.job_to_be_done),
                ("Market Size:", entry.market_and_opportunity.market_size),
                ("Why Now?:", entry.market_and_opportunity.why_now),
            ],
        )
    )
    blocks.extend(
        _section_block_with_bullets(
            "Team",
            [
                ("Founder Market Fit:", entry.team.founder_market_fit),
                (
                    "Superpower or Execution Advantage:",
                    entry.team.superpower_or_execution_advantage,
                ),
            ],
        )
    )
    blocks.extend(
        _section_block_with_bullets(
            "Key Metrics (Anonymized Ranges)",
            [
                ("ARR / Contracted Revenue:", entry.key_metrics.arr_or_contracted_revenue),
                ("Retention:", entry.key_metrics.retention),
                (
                    "Efficiency / Techno-Economics:",
                    entry.key_metrics.efficiency_or_techno_economics,
                ),
            ],
        )
    )
    if entry.competitive_moat is not None:
        blocks.extend(
            _section_block_with_bullets(
                "Competitive Moat & Company Superpower",
                [
                    (
                        "The Structural Advantage:",
                        entry.competitive_moat.structural_advantage,
                    ),
                    ("Execution Velocity:", entry.competitive_moat.execution_velocity),
                ],
            )
        )

    # Anti-Thesis bullets are full paragraphs (no bold-prefix sub-label).
    blocks.append(_section_banner("Anti-Thesis"))
    blocks.extend(Block(text=p, style="bullet") for p in entry.anti_thesis_paragraphs)

    blocks.extend(
        _section_block_with_bullets(
            "Bull Case",
            [
                ("Thesis:", entry.bull_case.thesis),
                ("Verdict:", entry.bull_case.verdict),
            ],
        )
    )
    return blocks


def _paragraph_with_bold_prefix(label: str, body: str) -> list[Block]:
    """Render a paragraph as `<label> <body>` with `<label>` bolded.

    Returns a single-element list so it composes naturally with `.extend`.
    """
    combined = f"{label} {body}"
    return [Block(text=combined, style="normal", bold_prefix_chars=len(label))]


def _section_banner(label: str) -> Block:
    """Bold paragraph standing alone — section banner not promoted to heading."""
    return Block(text=label, style="normal", bold_prefix_chars=len(label))


def _section_block_with_bullets(label: str, items: list[tuple[str, str]]) -> list[Block]:
    """Bold section banner followed by bullets where each item has a bold
    inline sub-label (e.g., 'Job(s) to be done: <body>')."""
    blocks: list[Block] = [_section_banner(label)]
    for sub_label, body in items:
        text = f"{sub_label} {body}"
        blocks.append(Block(text=text, style="bullet", bold_prefix_chars=len(sub_label)))
    return blocks


# ---------------------------------------------------------------------------
# Composer — text + relative-offset request metadata.
# ---------------------------------------------------------------------------


def _compose_blocks(
    blocks: list[Block],
) -> tuple[str, dict[str, list[Any]]]:
    """Flatten `blocks` to insertable text + relative-offset request metadata.

    Returns:
      text: the concatenated text to insertText
      meta: dict with keys
        - "paragraph_styles": list[(named_style, rel_start, rel_end)] for
          every block (we always set explicitly so a body block doesn't
          inherit the heading style of the preceding paragraph)
        - "bold_ranges": list[(rel_start, rel_end)] for bold-prefix runs
        - "bullet_ranges": list[(rel_start, rel_end)] grouping consecutive
          bullet blocks (one createParagraphBullets request per group)
    """
    pieces: list[str] = []
    paragraph_styles: list[tuple[str, int, int]] = []
    bold_ranges: list[tuple[int, int]] = []
    bullet_block_ranges: list[tuple[int, int]] = []

    cursor = 0
    for block in blocks:
        block_text = block.text + "\n"
        pieces.append(block_text)
        rel_start = cursor
        rel_end = cursor + len(block_text)

        named_style = _block_style_to_named(block.style)
        paragraph_styles.append((named_style, rel_start, rel_end))

        if block.bold_prefix_chars > 0:
            # Bold only the visible prefix, not the trailing newline.
            n = min(block.bold_prefix_chars, len(block.text))
            bold_ranges.append((rel_start, rel_start + n))

        if block.style == "bullet":
            bullet_block_ranges.append((rel_start, rel_end))

        cursor = rel_end

    bullet_ranges = _merge_adjacent_ranges(bullet_block_ranges)
    return "".join(pieces), {
        "paragraph_styles": paragraph_styles,
        "bold_ranges": bold_ranges,
        "bullet_ranges": bullet_ranges,
    }


def _block_style_to_named(style: BlockStyle) -> str:
    """Map our local BlockStyle to the Google Docs `namedStyleType`."""
    if style == "heading_2":
        return "HEADING_2"
    if style == "heading_3":
        return "HEADING_3"
    if style == "heading_4":
        return "HEADING_4"
    if style == "heading_5":
        return "HEADING_5"
    # Both "normal" and "bullet" use NORMAL_TEXT as the paragraph style;
    # bullets are an *additional* createParagraphBullets request applied on
    # top of NORMAL_TEXT.
    return "NORMAL_TEXT"


def _merge_adjacent_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge consecutive `(start, end)` ranges where end == next.start.

    Adjacent bullet paragraphs must be passed to `createParagraphBullets` as
    a single range so the Docs API treats them as one list. Non-adjacent
    bullet groups (e.g., bullets under one section and bullets under
    another) need separate requests."""
    if not ranges:
        return []
    out: list[tuple[int, int]] = [ranges[0]]
    for start, end in ranges[1:]:
        prev_start, prev_end = out[-1]
        if start == prev_end:
            out[-1] = (prev_start, end)
        else:
            out.append((start, end))
    return out


# ---------------------------------------------------------------------------
# Doc-API helpers.
# ---------------------------------------------------------------------------


def _find_container_heading_end(doc: dict[str, Any], text: str, level: int) -> int:
    """Return the document index just after the container heading paragraph.

    Matches on (text, HEADING_<level>) — inserting at the returned index
    places the new entry as the most recent under that container."""
    target_style = f"HEADING_{level}"
    content = doc.get("body", {}).get("content", [])
    for elem in content:
        para = elem.get("paragraph")
        if para is None:
            continue
        style = para.get("paragraphStyle", {}).get("namedStyleType", "")
        if style != target_style:
            continue
        para_text = "".join(
            run.get("textRun", {}).get("content", "") for run in para.get("elements", [])
        ).strip()
        if para_text == text:
            return int(elem["endIndex"])
    raise GoogleDocsStructureError(
        f"Document does not contain a {target_style} paragraph titled '{text}'. "
        f"Add it manually before publishing entries."
    )


def _load_credentials() -> Credentials:
    """Get a valid `Credentials` instance, prompting the browser flow on
    first run if no cached token exists. Cached token auto-refreshes on
    expiry."""
    token_path = config_dir() / _TOKEN_FILENAME
    creds_path = config_dir() / _CREDENTIALS_FILENAME

    creds: Credentials | None = None
    if token_path.is_file():
        creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

    if creds is not None and creds.valid:
        return creds

    if creds is not None and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    if not creds_path.is_file():
        raise GoogleDocsAuthError(
            f"Missing OAuth credentials at {creds_path}.\n"
            "1. Create a Google Cloud project (or reuse one)\n"
            "2. Enable the Google Docs API\n"
            "3. Create OAuth client credentials (Desktop app type)\n"
            "4. Download the credentials JSON and save it to the path above"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), _SCOPES)
    # Desktop-app flows always yield oauth2 Credentials at runtime; the stub's
    # union includes the external-account variant we never configure.
    creds = cast(Credentials, flow.run_local_server(port=0))
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds
