import pytest

from lead_match_runtime import job_runner


def _set_valid_env(monkeypatch):
    monkeypatch.setattr(job_runner, "EXPECTED_PROJECT", "ctoteam")
    monkeypatch.setattr(
        job_runner, "EXPECTED_CLOUDSQL_CONNECTION_NAME", "ctoteam:us-central1:lead-mgmt-db"
    )
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "ctoteam")
    monkeypatch.setenv("CLOUDSQL_CONNECTION_NAME", "ctoteam:us-central1:lead-mgmt-db")
    monkeypatch.setenv("ALLOW_CLIENT_GCP", "false")
    monkeypatch.setenv("ALLOW_PRODUCTION", "false")


class TestAssertIsolatedRuntime:
    def test_passes_with_correct_env(self, monkeypatch):
        _set_valid_env(monkeypatch)
        job_runner.assert_isolated_runtime()

    def test_fails_wrong_project(self, monkeypatch):
        _set_valid_env(monkeypatch)
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "wrong-project")
        with pytest.raises(RuntimeError, match="GOOGLE_CLOUD_PROJECT"):
            job_runner.assert_isolated_runtime()

    def test_fails_wrong_connection(self, monkeypatch):
        _set_valid_env(monkeypatch)
        monkeypatch.setenv("CLOUDSQL_CONNECTION_NAME", "wrong:connection:name")
        with pytest.raises(RuntimeError, match="CLOUDSQL_CONNECTION_NAME"):
            job_runner.assert_isolated_runtime()

    def test_allows_missing_connection(self, monkeypatch):
        _set_valid_env(monkeypatch)
        monkeypatch.delenv("CLOUDSQL_CONNECTION_NAME", raising=False)
        job_runner.assert_isolated_runtime()

    def test_fails_allow_client_gcp_true(self, monkeypatch):
        _set_valid_env(monkeypatch)
        monkeypatch.setenv("ALLOW_CLIENT_GCP", "true")
        with pytest.raises(RuntimeError, match="ALLOW_CLIENT_GCP"):
            job_runner.assert_isolated_runtime()

    def test_fails_allow_production_true(self, monkeypatch):
        _set_valid_env(monkeypatch)
        monkeypatch.setenv("ALLOW_PRODUCTION", "true")
        with pytest.raises(RuntimeError, match="ALLOW_PRODUCTION"):
            job_runner.assert_isolated_runtime()

    def test_fails_missing_allow_client_gcp(self, monkeypatch):
        _set_valid_env(monkeypatch)
        monkeypatch.delenv("ALLOW_CLIENT_GCP", raising=False)
        with pytest.raises(RuntimeError, match="ALLOW_CLIENT_GCP"):
            job_runner.assert_isolated_runtime()
