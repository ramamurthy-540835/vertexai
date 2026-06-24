import pytest

from lead_match_runtime.business_rules import WarehouseScope, get_warehouse_scope
from lead_match_runtime import job_runner


class TestGetWarehouseScope:
    def test_defaults_to_all(self, monkeypatch, business_rules):
        monkeypatch.delenv("WAREHOUSE", raising=False)
        monkeypatch.delenv("WAREHOUSE_SCOPE", raising=False)
        scope = get_warehouse_scope(business_rules)
        assert scope.is_all is True
        assert scope.values is None

    def test_warehouse_env_wins_over_warehouse_scope(self, monkeypatch, business_rules):
        monkeypatch.setenv("WAREHOUSE", "115")
        monkeypatch.setenv("WAREHOUSE_SCOPE", "569")
        scope = get_warehouse_scope(business_rules)
        assert scope.values == (115,)

    def test_warehouse_scope_fallback(self, monkeypatch, business_rules):
        monkeypatch.delenv("WAREHOUSE", raising=False)
        monkeypatch.setenv("WAREHOUSE_SCOPE", "569")
        scope = get_warehouse_scope(business_rules)
        assert scope.values == (569,)

    def test_explicit_all(self, monkeypatch, business_rules):
        monkeypatch.setenv("WAREHOUSE", "ALL")
        scope = get_warehouse_scope(business_rules)
        assert scope.is_all is True

    def test_comma_separated(self, monkeypatch, business_rules):
        monkeypatch.setenv("WAREHOUSE", "115,569")
        monkeypatch.delenv("WAREHOUSE_SCOPE", raising=False)
        scope = get_warehouse_scope(business_rules)
        assert scope.values == (115, 569)

    def test_invalid_non_digit(self, monkeypatch, business_rules):
        monkeypatch.setenv("WAREHOUSE", "abc")
        monkeypatch.delenv("WAREHOUSE_SCOPE", raising=False)
        with pytest.raises(ValueError, match="Invalid warehouse scope"):
            get_warehouse_scope(business_rules)

    def test_whitespace_trimmed(self, monkeypatch, business_rules):
        monkeypatch.setenv("WAREHOUSE", " 115 , 569 ")
        monkeypatch.delenv("WAREHOUSE_SCOPE", raising=False)
        scope = get_warehouse_scope(business_rules)
        assert scope.values == (115, 569)


class TestWarehouseSqlFilter:
    def test_all_warehouses_returns_empty(self, monkeypatch):
        monkeypatch.setattr(job_runner, "warehouse_scope", lambda: WarehouseScope(values=None))
        clause, params = job_runner.warehouse_sql_filter("t")
        assert clause == ""
        assert params == []

    def test_single_warehouse(self, monkeypatch):
        monkeypatch.setattr(job_runner, "warehouse_scope", lambda: WarehouseScope(values=(115,)))
        clause, params = job_runner.warehouse_sql_filter("t")
        assert clause == "AND t.warehouse_number IN (%s)"
        assert params == [115]

    def test_multiple_warehouses(self, monkeypatch):
        monkeypatch.setattr(job_runner, "warehouse_scope", lambda: WarehouseScope(values=(115, 569)))
        clause, params = job_runner.warehouse_sql_filter("l")
        assert clause == "AND l.warehouse_number IN (%s, %s)"
        assert params == [115, 569]

    def test_alias_substitution(self, monkeypatch):
        monkeypatch.setattr(job_runner, "warehouse_scope", lambda: WarehouseScope(values=(115,)))
        clause, _ = job_runner.warehouse_sql_filter("leads_embeddings")
        assert "leads_embeddings.warehouse_number" in clause
