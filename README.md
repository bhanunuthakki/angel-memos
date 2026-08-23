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
- **Templates (`templates/`)**: Stage-appropriate exit math sheets across valuation methods.

## Privacy & Anonymization

This repository contains only the engine, extension, scoring rubrics, and templates.
All confidential deal room PDFs, founder materials, and investor databases remain strictly
local and are ignored by git.
