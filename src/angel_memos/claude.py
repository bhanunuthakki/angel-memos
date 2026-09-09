"""Compatibility alias for the provider-neutral :mod:`angel_memos.llm` boundary."""

from __future__ import annotations

import sys

from angel_memos import llm as _llm

sys.modules[__name__] = _llm
