"""Unit tests for lead_match_runtime/exact_handoff.py."""

import io
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from lead_match_runtime.exact_handoff import (
    EXPECTED_COLUMNS,
    ExactHandoffResult,
    check_source_id_alignment,
    classify_rows,
    find_duplicate_pos,
    load_and_validate,
    normalize_df,
    validate_schema,
)


def _make_csv(rows: list[dict], path: Path | None = None) -> str:
    df = pd.DataFrame(rows)
    for col in EXPECTED_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[EXPECTED_COLUMNS]
    if path:
        df.to_csv(path, index=False)
        return str(path)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def _base_row(**overrides) -> dict:
    base = {
        "lead_id": "LEAD001",
        "pos_id": "POS001",
        "match_result": "Match",
        "similarity_score": "150",
        "winning_set": "4",
        "match_type": "Exact",
        "primary_transaction": "True",
        "matched_by": "System",
        "matching_comments": "",
        "closed_existing_flag": "False",
        "account_number": "123",
        "transaction_count": "1",
        "business_name_transaction": "Test Co",
        "membership_number": "MEM1",
        "warehouse_number": "115",
        "sales_reference_id": "SR1",
        "fiscal_year_transaction": "2026",
        "fiscal_period_transaction": "10",
        "week": "1",
        "shop_type": "Delivery",
        "bd_industry": "",
        "order_amount": "500",
        "industry_description": "",
        "first_name": "",
        "last_name": "",
        "address_line_one": "123 Main St",
        "address_line_two": "",
        "city": "Seattle",
        "state": "WA",
        "zip_code": "98101",
        "email": "test@test.com",
        "phone": "2065551234",
        "u_matched_lead_number": "LEAD001",
        "u_order_amount": "500",
        "u_order_amount_rounded": "500",
        "updated_date": "2026-06-23",
    }
    base.update(overrides)
    return base


class TestClassification:
    def test_score_150_is_final_exact(self):
        row = _base_row(similarity_score="150", match_result="Match")
        df = normalize_df(pd.DataFrame([row]))
        result = classify_rows(df)
        assert len(result["final_exact"]) == 1
        assert len(result["deterministic_potential"]) == 0

    def test_score_100_is_final_exact(self):
        row = _base_row(similarity_score="100", match_result="Match")
        df = normalize_df(pd.DataFrame([row]))
        result = classify_rows(df)
        assert len(result["final_exact"]) == 1

    def test_score_95_potential_not_final(self):
        row = _base_row(similarity_score="95", match_result="Potential", match_type="Exact")
        df = normalize_df(pd.DataFrame([row]))
        result = classify_rows(df)
        assert len(result["final_exact"]) == 0
        assert len(result["deterministic_potential"]) == 1

    def test_score_70_retained_for_rescore(self):
        row = _base_row(similarity_score="70", match_result="Potential", match_type="Exact")
        df = normalize_df(pd.DataFrame([row]))
        result = classify_rows(df)
        assert len(result["deterministic_potential"]) == 1

    def test_match_type_exact_alone_insufficient(self):
        """match_type=Exact with match_result=Potential should NOT be final_exact."""
        row = _base_row(similarity_score="85", match_result="Potential", match_type="Exact")
        df = normalize_df(pd.DataFrame([row]))
        result = classify_rows(df)
        assert len(result["final_exact"]) == 0
        assert len(result["deterministic_potential"]) == 1

    def test_ce_stub_blank_pos_valid(self):
        row = _base_row(pos_id="", closed_existing_flag="True", match_result="", similarity_score="0")
        df = normalize_df(pd.DataFrame([row]))
        result = classify_rows(df)
        assert len(result["closed_existing"]) == 1


class TestDuplicatePOS:
    def test_unique_highest_owner(self):
        rows = [
            _base_row(lead_id="L1", pos_id="P1", similarity_score="150"),
            _base_row(lead_id="L2", pos_id="P1", similarity_score="120"),
        ]
        df = normalize_df(pd.DataFrame(rows))
        final = classify_rows(df)["final_exact"]
        conflicts, ambiguous = find_duplicate_pos(final)
        assert not conflicts.empty
        assert len(ambiguous) == 0

    def test_tied_highest_creates_conflict(self):
        rows = [
            _base_row(lead_id="L1", pos_id="P1", similarity_score="150"),
            _base_row(lead_id="L2", pos_id="P1", similarity_score="150"),
        ]
        df = normalize_df(pd.DataFrame(rows))
        final = classify_rows(df)["final_exact"]
        conflicts, ambiguous = find_duplicate_pos(final)
        assert "P1" in ambiguous

    def test_duplicate_potential_available_for_semantic(self):
        rows = [
            _base_row(lead_id="L1", pos_id="P1", similarity_score="85", match_result="Potential"),
            _base_row(lead_id="L2", pos_id="P1", similarity_score="80", match_result="Potential"),
        ]
        df = normalize_df(pd.DataFrame(rows))
        result = classify_rows(df)
        assert len(result["deterministic_potential"]) == 2
        assert len(result["final_exact"]) == 0


class TestSourceIDAlignment:
    def _make_result(self, final_leads, final_pos, pot_leads=None, ce_leads=None):
        return ExactHandoffResult(
            source_uri="test.csv",
            row_count=0,
            final_exact_rows=pd.DataFrame(),
            deterministic_potential_rows=pd.DataFrame(),
            closed_existing_rows=pd.DataFrame(),
            invalid_rows=pd.DataFrame(),
            final_exact_lead_ids=set(final_leads),
            final_exact_pos_ids=set(final_pos),
            deterministic_potential_lead_ids=set(pot_leads or []),
            deterministic_potential_pos_ids=set(),
            closed_existing_lead_ids=set(ce_leads or []),
            duplicate_pos_groups=pd.DataFrame(),
            ambiguous_exact_pos_ids=set(),
        )

    def test_zero_overlap_fails(self):
        result = self._make_result(["LEAD001"], ["POS001"])
        alignment = check_source_id_alignment(result, {"LEADXXX"}, {"POSXXX"})
        assert not alignment["pass"]
        assert alignment["lead_id_overlap_percentage"] == 0

    def test_regenerated_ids_fail(self):
        result = self._make_result(["LEAD00603655"], ["GPOS44205023"])
        mock_leads = {"LEAD7F621C5100000001", "LEAD7F621C5100000002"}
        mock_pos = {"POS1E061DA600000001"}
        alignment = check_source_id_alignment(result, mock_leads, mock_pos)
        assert not alignment["pass"]

    def test_matching_ids_pass(self):
        result = self._make_result(["LEAD001", "LEAD002"], ["POS001", "POS002"])
        alignment = check_source_id_alignment(result, {"LEAD001", "LEAD002"}, {"POS001", "POS002"})
        assert alignment["pass"]
        assert alignment["lead_id_overlap_percentage"] == 100.0


class TestScoreScaleSeparation:
    def test_exact_150_never_changes_fuzzy_rules(self):
        from lead_match_runtime.business_rules import fuzzy_max_score, fuzzy_qualify_min_score, load_business_rules
        rules = load_business_rules()
        assert fuzzy_max_score(rules) == 99.999
        assert fuzzy_qualify_min_score(rules) == 70

    def test_fuzzy_caps_at_99_999(self):
        from lead_match_runtime.business_rules import normalize_fuzzy_final_score, load_business_rules
        rules = load_business_rules()
        result = normalize_fuzzy_final_score(105.0, config=rules)
        assert result is not None
        assert result <= 99.999


class TestLoadAndValidate:
    def test_full_load(self, tmp_path):
        rows = [
            _base_row(lead_id="L1", pos_id="P1", similarity_score="150", match_result="Match"),
            _base_row(lead_id="L2", pos_id="P2", similarity_score="85", match_result="Potential"),
            _base_row(lead_id="L3", pos_id="", closed_existing_flag="True", match_result="", similarity_score="0"),
        ]
        csv_path = tmp_path / "test.csv"
        _make_csv(rows, csv_path)
        result = load_and_validate(str(csv_path), warehouse_number=115)
        assert result.row_count == 3
        assert len(result.final_exact_rows) == 1
        assert len(result.deterministic_potential_rows) == 1
        assert len(result.closed_existing_rows) == 1
        assert "L1" in result.final_exact_lead_ids
        assert "P2" in result.deterministic_potential_pos_ids
        assert "L3" in result.closed_existing_lead_ids

    def test_exact_uri_consumed_by_result(self, tmp_path):
        csv_path = tmp_path / "output.csv"
        _make_csv([_base_row()], csv_path)
        result = load_and_validate(str(csv_path))
        assert result.source_uri == str(csv_path)

    def test_ce_warehouse_from_raw_lead(self):
        """CE rows with blank warehouse should be recoverable from raw lead source."""
        row = _base_row(pos_id="", warehouse_number="", closed_existing_flag="True",
                        match_result="", similarity_score="0")
        df = normalize_df(pd.DataFrame([row]))
        result = classify_rows(df)
        assert len(result["closed_existing"]) == 1
