import pytest

from lead_match_runtime import job_runner


class TestDbConfig:
    def test_cloud_sql_socket(self, monkeypatch):
        monkeypatch.setenv("CLOUDSQL_CONNECTION_NAME", "ctoteam:us-central1:lead-mgmt-db")
        monkeypatch.setenv("CLOUDSQL_SOCKET_DIR", "/cloudsql")
        monkeypatch.setenv("DB_NAME", "postgres")
        monkeypatch.setenv("DB_USER", "postgres")
        monkeypatch.setenv("DB_PASSWORD", "test")
        config = job_runner.db_config()
        assert config["unix_sock"] == "/cloudsql/ctoteam:us-central1:lead-mgmt-db/.s.PGSQL.5432"
        assert config["database"] == "postgres"
        assert config["user"] == "postgres"
        assert config["password"] == "test"

    def test_custom_socket_dir(self, monkeypatch):
        monkeypatch.setenv("CLOUDSQL_CONNECTION_NAME", "ctoteam:us-central1:lead-mgmt-db")
        monkeypatch.setenv("CLOUDSQL_SOCKET_DIR", "/tmp/cloudsql")
        monkeypatch.setenv("DB_NAME", "postgres")
        monkeypatch.setenv("DB_USER", "postgres")
        monkeypatch.setenv("DB_PASSWORD", "test")
        config = job_runner.db_config()
        assert config["unix_sock"].startswith("/tmp/cloudsql/")

    def test_local_fallback(self, monkeypatch):
        monkeypatch.delenv("CLOUDSQL_CONNECTION_NAME", raising=False)
        monkeypatch.setenv("ALLOW_LOCAL_DB", "true")
        monkeypatch.setenv("DB_HOST", "127.0.0.1")
        monkeypatch.setenv("DB_PORT", "5432")
        monkeypatch.setenv("DB_NAME", "postgres")
        monkeypatch.setenv("DB_USER", "postgres")
        monkeypatch.setenv("DB_PASSWORD", "test")
        config = job_runner.db_config()
        assert config["host"] == "127.0.0.1"
        assert config["port"] == 5432

    def test_local_fallback_blocked(self, monkeypatch):
        monkeypatch.delenv("CLOUDSQL_CONNECTION_NAME", raising=False)
        monkeypatch.setenv("ALLOW_LOCAL_DB", "false")
        monkeypatch.setenv("DB_NAME", "postgres")
        monkeypatch.setenv("DB_USER", "postgres")
        monkeypatch.setenv("DB_PASSWORD", "test")
        with pytest.raises(RuntimeError, match="ALLOW_LOCAL_DB"):
            job_runner.db_config()

    def test_missing_password(self, monkeypatch):
        monkeypatch.setenv("CLOUDSQL_CONNECTION_NAME", "ctoteam:us-central1:lead-mgmt-db")
        monkeypatch.setenv("DB_NAME", "postgres")
        monkeypatch.setenv("DB_USER", "postgres")
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        with pytest.raises(RuntimeError, match="DB_PASSWORD"):
            job_runner.db_config()

    def test_missing_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("CLOUDSQL_CONNECTION_NAME", "ctoteam:us-central1:lead-mgmt-db")
        monkeypatch.delenv("DB_NAME", raising=False)
        monkeypatch.delenv("DB_USER", raising=False)
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        with pytest.raises(RuntimeError, match="DB_NAME"):
            job_runner.db_config()

    def test_local_missing_host(self, monkeypatch):
        monkeypatch.delenv("CLOUDSQL_CONNECTION_NAME", raising=False)
        monkeypatch.setenv("ALLOW_LOCAL_DB", "true")
        monkeypatch.delenv("DB_HOST", raising=False)
        monkeypatch.setenv("DB_NAME", "postgres")
        monkeypatch.setenv("DB_USER", "postgres")
        monkeypatch.setenv("DB_PASSWORD", "test")
        with pytest.raises(RuntimeError, match="DB_HOST"):
            job_runner.db_config()
