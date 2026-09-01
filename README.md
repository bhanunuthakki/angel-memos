# Angel Memos

A Chrome extension and research pipeline for angel-stage deals. One click on an
AngelList page pulls down the deal memo and pitch deck, then runs structured
diligence, financial modeling, and an adversarial bear case.

## What it does

1. **One-click capture**: An MV3 Chrome extension captures the deal memo PDF,
   downloads attached pitch decks, and structures the incoming deal files.
2. **Automated screening & scoring**: Scores deals against an archetype-specific
   evaluation rubric and generates a list of diligence questions before investor calls.
3. **The two-document split**: Generates a detailed private memo for personal records,
   plus an automatically masked public memo safe to share with co-investors (company
   names replaced with category descriptors, founders reduced to titles, dollar amounts
   bucketed into ranges).
4. **Exit math modeling**: Automatically populates valuation and exit-math spreadsheets
   anchored to real market comparables.

## Architecture

- **CLI (`src/angel_memos/cli.py`)**: Python tool for diligence generation, rubric scoring,
  and memo rendering.
- **Chrome Extension (`extension/`)**: Manifest V3 extension for deal capture on AngelList.
- **Diligence & LLM Synthesis**: Schema-governed LLM workflows that pressure-test investment
  theses and generate adversarial counter-arguments.

## Quick start

Requires Python 3.13 or later.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
angel-memos --help
```

Enable the local public-boundary check before pushing:

```bash
git config core.hooksPath .githooks
```

This is a local, single-user research tool. It is not a hosted service, and the
repository does not include private deal-room files or spreadsheet templates.

## Privacy & Anonymization

This repository contains only the engine, extension, and scoring rubrics.
All confidential deal room PDFs, founder materials, and investor databases remain strictly
local and are ignored by git.

Automated masking is not a guarantee of anonymity. A human must review every
generated public memo before sharing it. Confidential source files must never
enter Git, even temporarily. The checked-in financial examples are synthetic,
not investment advice, and do not describe a real deal.
