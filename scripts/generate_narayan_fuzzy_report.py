#!/usr/bin/env python3
"""Backward-compatible wrapper for the renamed business report generator."""

from __future__ import annotations

from generate_fuzzy_match_business_report import main


if __name__ == "__main__":
    raise SystemExit(main())
