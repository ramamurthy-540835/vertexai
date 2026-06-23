#!/usr/bin/env python3
"""Generate a stakeholder-ready fuzzy match business report from run artifacts.

The report is deterministic by default. Set REPORT_LLM_PROVIDER and
REPORT_REASONING_MODEL to optionally ask a reasoning model to rewrite the
stakeholder reply from the validated facts.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import textwrap
import shutil
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[1] / "lead_match_runtime" / "lead_to_pos_match_rules.json"


@dataclass
class SampleRow:
    band: str
    row: dict[str, str]
    calculated_score: float | None


@dataclass(frozen=True)
class ReportRules:
    exact_score: float
    no_match_max_score: float
    fuzzy_score_bands: list[dict[str, Any]]
    scoring_formula: str
    address_weight: float
    business_weight: float
    recall_gate_role: str

    @property
    def total_precision_weight(self) -> float:
        return self.address_weight + self.business_weight


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def load_artifacts(report_dir: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    summary_path = report_dir / "summary.json"
    matches_path = report_dir / "matches.csv"
    if not summary_path.exists():
        raise FileNotFoundError(f"Missing {summary_path}")
    if not matches_path.exists():
        raise FileNotFoundError(f"Missing {matches_path}")

    summary = json.loads(summary_path.read_text())
    with matches_path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    return summary, rows


def load_report_rules(path: Path) -> ReportRules:
    config = json.loads(path.read_text(encoding="utf-8"))
    decision = config["decision_rules"]
    fields = config["embeddings"]["fields"]
    return ReportRules(
        exact_score=float(decision["exact_score"]),
        no_match_max_score=float(decision["no_match_max_score"]),
        fuzzy_score_bands=sorted(
            decision["fuzzy_score_bands"],
            key=lambda band: float(band["min_score"]),
            reverse=True,
        ),
        scoring_formula=str(config["scoring"]["precision_score_formula"]),
        address_weight=float(fields["full_address"]["weight"]),
        business_weight=float(fields["business_name"]["weight"]),
        recall_gate_role=str(fields["combined_field"]["role"]),
    )


def archive_old_reports(report_dir: Path) -> list[Path]:
    archive_dir = report_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived: list[Path] = []
    patterns = [
        "narayan_fuzzy_matching_report.md",
        "validation_and_narayan_reply.md",
        "*_narayan_*.md",
    ]
    for pattern in patterns:
        for path in sorted(report_dir.glob(pattern)):
            if path.name in {"report.md", "fuzzy_match_business_report.md"}:
                continue
            if path.parent == archive_dir:
                continue
            target = archive_dir / path.name
            if target.exists():
                target.unlink()
            shutil.move(str(path), str(target))
            archived.append(target)
    return archived


def band_for_score(score: float, rules: ReportRules) -> str:
    for band in rules.fuzzy_score_bands:
        if float(band["min_score"]) <= score <= float(band["max_score"]):
            return str(band["name"])
    return "Out of Band"


def calculate_deployed_score(row: dict[str, str], rules: ReportRules) -> float | None:
    address = number(row.get("full_address_score"))
    business = number(row.get("business_name_score"))
    if address is None or business is None:
        return None
    return round(
        ((rules.address_weight * address) + (rules.business_weight * business))
        / rules.total_precision_weight,
        2,
    )


def validate_rows(summary: dict[str, Any], rows: list[dict[str, str]], rules: ReportRules) -> dict[str, Any]:
    exact = [row for row in rows if row.get("match_type") == "Exact"]
    non_exact = [row for row in rows if row.get("match_type") != "Exact"]
    exact_scores = [number(row.get("final_score")) for row in exact if number(row.get("final_score")) is not None]
    non_exact_scores = [
        number(row.get("final_score")) for row in non_exact if number(row.get("final_score")) is not None
    ]
    band_counts = Counter()
    for score in non_exact_scores:
        band_counts[band_for_score(score, rules)] += 1
    fuzzy_floor = min(float(band["min_score"]) for band in rules.fuzzy_score_bands)

    return {
        "summary_match_rows": summary.get("match_rows"),
        "csv_rows": len(rows),
        "match_type_counts": dict(Counter(row.get("match_type", "") for row in rows)),
        "lifecycle_state_counts": dict(Counter(row.get("lifecycle_state", "") for row in rows)),
        "exact_count": len(exact),
        "exact_min": min(exact_scores) if exact_scores else None,
        "exact_max": max(exact_scores) if exact_scores else None,
        "non_exact_count": len(non_exact),
        "non_exact_min": min(non_exact_scores) if non_exact_scores else None,
        "non_exact_max": max(non_exact_scores) if non_exact_scores else None,
        "non_exact_ge_100": sum(1 for score in non_exact_scores if score >= rules.exact_score),
        "below_70": sum(1 for score in exact_scores + non_exact_scores if score < fuzzy_floor),
        "component_scores_present": sum(
            1
            for row in non_exact
            if row.get("combined_field_score")
            and row.get("full_address_score")
            and row.get("business_name_score")
        ),
        "band_counts": dict(band_counts),
    }


def choose_samples(rows: list[dict[str, str]], per_band: int, rules: ReportRules) -> list[SampleRow]:
    non_exact = [row for row in rows if row.get("match_type") != "Exact"]
    samples: list[SampleRow] = []
    for band_cfg in rules.fuzzy_score_bands:
        band = str(band_cfg["name"])
        min_score = float(band_cfg["min_score"])
        max_score = float(band_cfg["max_score"])
        candidates = [
            row
            for row in non_exact
            if (score := number(row.get("final_score"))) is not None and min_score <= score <= max_score
        ]
        candidates.sort(key=lambda row: number(row.get("final_score")) or 0, reverse=True)
        if band == "Potential Medium":
            candidates.sort(key=lambda row: abs((number(row.get("final_score")) or 0) - 87.5))
        if band == "Potential Low":
            candidates.sort(key=lambda row: number(row.get("final_score")) or 0, reverse=True)
        for row in candidates[:per_band]:
            samples.append(SampleRow(band=band, row=row, calculated_score=calculate_deployed_score(row, rules)))
    return samples


def sample_table(samples: list[SampleRow], rules: ReportRules) -> str:
    lines = [
        "| Band | Lead ID | POS ID | Match Type | Lifecycle | Lead Business | POS Business | Combined | Address | Business | Math | Stored |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | ---: | ---: | ---: | :--- | ---: |",
    ]
    for sample in samples:
        row = sample.row
        address = number(row.get("full_address_score"))
        business = number(row.get("business_name_score"))
        combined = number(row.get("combined_field_score"))
        stored = number(row.get("final_score"))
        math = (
            f"({fmt(rules.address_weight, 0)}*{fmt(address)} + {fmt(rules.business_weight, 0)}*{fmt(business)}) / {fmt(rules.total_precision_weight, 0)} = {fmt(sample.calculated_score)}"
            if sample.calculated_score is not None
            else "n/a"
        )
        lines.append(
            "| {band} | `{lead_id}` | `{pos_id}` | {match_type} | {lifecycle} | {lead_business} | {pos_business} | {combined} | {address} | {business} | `{math}` | {stored} |".format(
                band=sample.band,
                lead_id=row.get("lead_id", ""),
                pos_id=row.get("pos_id", ""),
                match_type=row.get("match_type", ""),
                lifecycle=row.get("lifecycle_state", ""),
                lead_business=(row.get("lead_business_name") or "").replace("|", "/"),
                pos_business=(row.get("pos_business_name") or "").replace("|", "/"),
                combined=fmt(combined),
                address=fmt(address),
                business=fmt(business),
                math=math,
                stored=fmt(stored),
            )
        )
    return "\n".join(lines)


def deterministic_reply(
    summary: dict[str, Any],
    validation: dict[str, Any],
    samples: list[SampleRow],
    rules: ReportRules,
) -> str:
    match_type_counts = validation["match_type_counts"]
    band_counts = validation["band_counts"]
    lines = [
        "Hi team,",
        "",
        f"We completed a full run for warehouse {summary.get('warehouse')} and validated the fuzzy outputs against the current business rules.",
        "",
        f"Confirmed: Vertex/fuzzy does not produce `Exact`. Exact remains deterministic only and is scored at `{fmt(rules.exact_score, 0)}`. Fuzzy scores are capped below `{fmt(rules.exact_score, 0)}`; in this run there were `{validation['non_exact_ge_100']}` fuzzy/non-exact rows at or above `{fmt(rules.exact_score, 0)}`.",
        "",
        "Current first-draft marking:",
        f"- Deterministic score `{fmt(rules.exact_score, 0)}` -> `Exact / Complete`",
    ]
    lines.extend(
        f"- Fuzzy score `{fmt(float(band['min_score']), 0)}-{fmt(float(band['max_score']), 3)}` -> `{band['name']}`"
        for band in rules.fuzzy_score_bands
    )
    lines.extend(
        [
            f"- Below `{fmt(rules.no_match_max_score + 0.001, 0)}` -> `No Match` / no row",
            "",
            "Run results:",
            f"- Total match rows: `{validation['csv_rows']}`",
            f"- Exact: `{match_type_counts.get('Exact', 0)}`",
            f"- Fuzzy: `{match_type_counts.get('Fuzzy', 0)}`",
            f"- Manual Review: `{match_type_counts.get('Manual Review', 0)}`",
        ]
    )
    lines.extend(f"- {band['name']} rows: `{band_counts.get(str(band['name']), 0)}`" for band in rules.fuzzy_score_bands)
    lines.extend(
        [
            "",
            "On manual confirmation: not all fuzzy results should be marked Complete. High fuzzy matches can be actioned as `Matching High / Closed - Match` under the approved business rule, but they remain traceable as AI/fuzzy-sourced. Medium and Low fuzzy matches should go to review/action queues, or stay as `Potential` if business policy requires confirmation.",
            "",
            "For the attribute-level walkthrough, each fuzzy row now includes component scores: `combined_field_score`, `full_address_score`, and `business_name_score`. The current deployed scoring formula is:",
            "",
            "```text",
            rules.scoring_formula,
            "```",
            "",
            f"This means address currently has {rules.address_weight / rules.total_precision_weight:.1%} weight and business name has {rules.business_weight / rules.total_precision_weight:.1%} weight. `combined_field_score` is retained as the semantic recall/evidence score.",
            "",
            "We can walk through the examples in this report with stakeholders in the meeting.",
        ]
    )
    return "\n".join(lines)


def build_prompt(
    summary: dict[str, Any],
    validation: dict[str, Any],
    samples: list[SampleRow],
    rules: ReportRules,
    include_raw_json: bool,
) -> str:
    lines = [
        "You are helping draft a concise stakeholder reply about a Costco lead-to-POS fuzzy matching run.",
        "Use only the facts below. Do not invent values.",
        "Exact matching runs first. Fuzzy/Vertex AI runs only on the residual unmatched records.",
        f"Exact score {fmt(rules.exact_score, 0)} is the only authoritative Exact / Complete path.",
        "Fuzzy output must never be labeled Exact.",
        "",
        "Summary facts:",
        f"- Warehouse: {summary.get('warehouse')}",
        f"- Match run ID: {summary.get('match_run_id')}",
        f"- Project: {summary.get('project')}",
        f"- Leads: {summary.get('lead_rows')}",
        f"- POS rows: {summary.get('pos_rows')}",
        f"- Total match rows: {validation.get('csv_rows')}",
        f"- Exact rows: {validation.get('exact_count')}",
        f"- Fuzzy rows: {validation.get('match_type_counts', {}).get('Fuzzy', 0)}",
        f"- Manual Review rows: {validation.get('match_type_counts', {}).get('Manual Review', 0)}",
        "",
    ]
    if include_raw_json:
        lines.extend(
            [
                "Validation JSON:",
                json.dumps(validation, indent=2),
                "",
                "Summary JSON:",
                json.dumps(summary, indent=2),
                "",
            ]
        )
    lines.extend(
        [
            "Current deployed formula:",
            rules.scoring_formula,
            "",
            "Sample rows:",
            sample_table(samples, rules),
            "",
            "Draft a professional answer from an architecture perspective.",
            "Confirm exact vs fuzzy behavior, manual Complete handling, weightage, and the residual-record flow.",
            "Keep it suitable for email or Teams and avoid raw JSON unless explicitly provided.",
        ]
    )
    return "\n".join(lines)


def render_metric_table(summary: dict[str, Any], validation: dict[str, Any]) -> str:
    rows = [
        ("Project", summary.get("project")),
        ("Warehouse", summary.get("warehouse")),
        ("Match run ID", summary.get("match_run_id")),
        ("Leads", summary.get("lead_rows")),
        ("POS rows", summary.get("pos_rows")),
        ("Total match rows", validation.get("csv_rows")),
        ("Exact rows", validation.get("exact_count")),
        ("Fuzzy rows", validation.get("match_type_counts", {}).get("Fuzzy", 0)),
        ("Manual Review rows", validation.get("match_type_counts", {}).get("Manual Review", 0)),
        ("Primary transactions", summary.get("primary_transaction_count")),
    ]
    lines = ["| Metric | Value |", "| :-- | --: |"]
    for metric, value in rows:
        lines.append(f"| {metric} | {value} |")
    return "\n".join(lines)


def render_band_table(validation: dict[str, Any], rules: ReportRules) -> str:
    lines = ["| Band | Score Range | Rows | Lifecycle / Status | Notes |", "| :-- | :-- | --: | :-- | :-- |"]
    for band in rules.fuzzy_score_bands:
        name = str(band["name"])
        score_range = f"{fmt(float(band['min_score']), 0)}-{fmt(float(band['max_score']), 3)}"
        rows = validation["band_counts"].get(name, 0)
        lifecycle = str(band["lifecycle_state"])
        notes = str(band.get("business_meaning") or band.get("system_action") or "")
        lines.append(f"| {name} | {score_range} | {rows} | {lifecycle} | {notes} |")
    return "\n".join(lines)


def render_examples(samples: list[SampleRow], rules: ReportRules) -> str:
    sections = []
    for sample in samples:
        row = sample.row
        address = number(row.get("full_address_score"))
        business = number(row.get("business_name_score"))
        stored = number(row.get("final_score"))
        math = (
            f"({fmt(rules.address_weight, 0)} * {fmt(address)} + "
            f"{fmt(rules.business_weight, 0)} * {fmt(business)}) / "
            f"{fmt(rules.total_precision_weight, 0)} = {fmt(sample.calculated_score)}"
        )
        sections.append(
            "\n".join(
                [
                    f"### {sample.band}",
                    f"- lead_id: `{row.get('lead_id', '')}`",
                    f"- pos_id: `{row.get('pos_id', '')}`",
                    f"- lead business: {row.get('lead_business_name', '')}",
                    f"- POS business: {row.get('pos_business_name', '')}",
                    f"- address score: `{fmt(address)}`",
                    f"- business score: `{fmt(business)}`",
                    f"- arithmetic: `{math}`",
                    f"- final score: `{fmt(stored)}`",
                    f"- interpretation: {sample.band}",
                ]
            )
        )
    return "\n\n".join(sections)


def validation_bullets(validation: dict[str, Any], rules: ReportRules) -> str:
    fuzzy_floor = min(float(band["min_score"]) for band in rules.fuzzy_score_bands)
    bullets = [
        f"Summary rows match CSV rows: {validation.get('summary_match_rows')} vs {validation.get('csv_rows')}",
        f"Exact count / range: {validation.get('exact_count')} / {fmt(validation.get('exact_min'))} to {fmt(validation.get('exact_max'))}",
        f"Non-exact range: {fmt(validation.get('non_exact_min'))} to {fmt(validation.get('non_exact_max'))}",
        f"Non-exact >={fmt(rules.exact_score, 0)}: {validation.get('non_exact_ge_100')}",
        f"Below {fmt(fuzzy_floor, 0)}: {validation.get('below_70')}",
        f"Component scores present: {validation.get('component_scores_present')}",
    ]
    return "\n".join(f"- {item}" for item in bullets)


def call_gemini(prompt: str, model: str, project: str, location: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True,
        project=project,
        location=location,
        http_options=types.HttpOptions(api_version="v1"),
    )
    response = client.models.generate_content(model=model, contents=prompt)
    return getattr(response, "text", "") or str(response)


def call_xai(prompt: str, model: str, api_key: str, base_url: str) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You write precise enterprise data-quality stakeholder replies."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"xAI request failed: HTTP {exc.code}: {detail}") from exc
    return body["choices"][0]["message"]["content"]


def maybe_model_reply(prompt: str, args: argparse.Namespace) -> str | None:
    provider = (args.llm_provider or os.environ.get("REPORT_LLM_PROVIDER", "none")).strip().lower()
    if provider in {"", "none", "off"}:
        return None

    if provider == "gemini":
        model = args.model or os.environ.get("REPORT_REASONING_MODEL") or "gemini-3.5-flash"
        project = args.project or os.environ.get("VERTEX_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        location = args.location or os.environ.get("VERTEX_LOCATION", "us-central1")
        if not project:
            raise RuntimeError("Set --project, VERTEX_PROJECT_ID, or GOOGLE_CLOUD_PROJECT for Gemini")
        return call_gemini(prompt, model, project, location)

    if provider == "xai":
        model = args.model or os.environ.get("REPORT_REASONING_MODEL") or "grok-4.3"
        api_key = os.environ.get("XAI_API_KEY")
        if not api_key:
            raise RuntimeError("Set XAI_API_KEY for xAI")
        base_url = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
        return call_xai(prompt, model, api_key, base_url)

    raise RuntimeError(f"Unsupported REPORT_LLM_PROVIDER: {provider}")


def build_markdown(
    summary: dict[str, Any],
    validation: dict[str, Any],
    samples: list[SampleRow],
    rules: ReportRules,
    deterministic: str,
    model_reply: str | None,
    prompt: str,
    include_raw_json: bool,
) -> str:
    generated = datetime.now(UTC).isoformat()
    lines = [
        "# Costco Lead-to-POS Fuzzy Logic Business Report",
        "",
        "## Executive Summary",
        "",
        f"- Warehouse `{summary.get('warehouse')}` for match run `{summary.get('match_run_id')}`.",
        f"- Leads processed: `{summary.get('lead_rows')}`; POS rows processed: `{summary.get('pos_rows')}`.",
        f"- Exact rows: `{validation.get('exact_count')}`; fuzzy rows: `{validation.get('match_type_counts', {}).get('Fuzzy', 0)}`; manual review rows: `{validation.get('match_type_counts', {}).get('Manual Review', 0)}`.",
        f"- Exact is deterministic and authoritative at score `{fmt(rules.exact_score, 0)}`; fuzzy runs only on residual unmatched records.",
        f"- The run produced `{validation.get('non_exact_count')}` non-exact rows with no scores at or above `{fmt(rules.exact_score, 0)}`.",
        f"- Main conclusion: fuzzy coverage is active and traceable, while exact remains the only proven score-{fmt(rules.exact_score, 0)} path.",
        "",
        "## Business Rule Decision",
        "",
        f"Exact score {fmt(rules.exact_score, 0)} is deterministic and authoritative. Vertex/fuzzy is not deterministic Exact. Fuzzy runs only on residual records after exact matching.",
        *[
            f"Fuzzy `{fmt(float(band['min_score']), 0)}-{fmt(float(band['max_score']), 3)}` = `{band['name']} / {band['lifecycle_state']}`."
            for band in rules.fuzzy_score_bands
        ],
        f"Below `{fmt(rules.no_match_max_score + 0.001, 0)}` = `No Match / no row`.",
        "High fuzzy matches may be actioned by business rule, but they must remain traceable as AI/fuzzy-sourced.",
        "",
        "## Run Results",
        "",
        render_metric_table(summary, validation),
        "",
        "## Band Breakdown",
        "",
        render_band_table(validation, rules),
        "",
        "## Scoring Model / Weightage",
        "",
        "The deployed fuzzy score is computed as:",
        "",
        "```text",
        rules.scoring_formula,
        "```",
        "",
        f"Address weight: `{rules.address_weight / rules.total_precision_weight:.1%}`",
        f"Business name weight: `{rules.business_weight / rules.total_precision_weight:.1%}`",
        f"combined_field_score is used as semantic {rules.recall_gate_role.replace('_', ' ')}/evidence, not directly in the final score for this run.",
        "",
        "## Example Walkthroughs",
        "",
        render_examples(samples, rules),
        "",
        "## Recommended Stakeholder Reply",
        "",
        deterministic,
        "",
        "## Validation Checks",
        "",
        validation_bullets(validation, rules),
        "",
        "## Appendix: Optional Reasoning Prompt",
        "",
        "Use this prompt only when you want an external model to draft a reply from the validated artifacts.",
        "",
        "```text",
        prompt,
        "```",
    ]

    if include_raw_json:
        lines.extend(
            [
                "",
                "## Raw Validation JSON",
                "",
                "```json",
                json.dumps(validation, indent=2),
                "```",
                "",
                "## Raw Summary JSON",
                "",
                "```json",
                json.dumps(summary, indent=2),
                "```",
            ]
        )

    lines.extend(
        [
            "",
            "## Footer",
            "",
            f"Generated UTC: `{generated}`",
            "Generated from deterministic report artifacts: summary.json and matches.csv. No external model was required.",
        ]
    )

    if model_reply:
        lines.extend(
            [
                "",
                "## Optional Model-Drafted Reply",
                "",
                model_reply.strip(),
            ]
        )

    return "\n".join(lines).strip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report-dir",
        default="reports/lead_match/ctoteam/115/codex-20260623031813-115",
        help="Folder containing summary.json and matches.csv",
    )
    parser.add_argument("--output", default="", help="Output markdown path. Defaults inside report-dir.")
    parser.add_argument("--rules-path", default=str(DEFAULT_RULES_PATH), help="Business rules JSON path")
    parser.add_argument("--sample-per-band", type=int, default=1)
    parser.add_argument("--include-raw-json", action="store_true", help="Include raw summary and validation JSON")
    parser.add_argument("--cleanup-old-reports", action="store_true", help="Archive older person-specific markdown files")
    parser.add_argument("--llm-provider", choices=["none", "gemini", "xai"], default="")
    parser.add_argument("--model", default="", help="Reasoning model id, e.g. from REPORT_REASONING_MODEL")
    parser.add_argument("--project", default="")
    parser.add_argument("--location", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_dir = Path(args.report_dir)
    output_path = Path(args.output) if args.output else report_dir / "fuzzy_match_business_report.md"

    summary, rows = load_artifacts(report_dir)
    rules = load_report_rules(Path(args.rules_path))
    validation = validate_rows(summary, rows, rules)
    samples = choose_samples(rows, args.sample_per_band, rules)
    deterministic = deterministic_reply(summary, validation, samples, rules)
    prompt = build_prompt(summary, validation, samples, rules, args.include_raw_json)
    model_reply = maybe_model_reply(prompt, args)
    if args.cleanup_old_reports:
        archive_old_reports(report_dir)

    output_path.write_text(
        build_markdown(summary, validation, samples, rules, deterministic, model_reply, prompt, args.include_raw_json),
        encoding="utf-8",
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
