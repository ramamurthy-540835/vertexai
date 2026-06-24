import os
from unittest.mock import MagicMock

import pytest

from lead_match_runtime.business_rules import load_business_rules


@pytest.fixture(scope="session")
def business_rules():
    return load_business_rules()


@pytest.fixture()
def mock_cursor():
    cursor = MagicMock()
    cursor.fetchone.return_value = (0,)
    cursor.fetchall.return_value = []
    return cursor


@pytest.fixture()
def mock_connection(mock_cursor):
    conn = MagicMock()
    conn.cursor.return_value = mock_cursor
    return conn


@pytest.fixture()
def skip_unless_cloudsql():
    if os.environ.get("RUN_CLOUDSQL_INTEGRATION_TESTS") != "true":
        pytest.skip("RUN_CLOUDSQL_INTEGRATION_TESTS is not set to 'true'")
