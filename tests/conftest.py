import os

import pytest


@pytest.fixture(autouse=True)
def use_test_database(monkeypatch):
    # A queue poll can claim any eligible row. Keep it away from development tickets.
    monkeypatch.setenv("MYSQL_DATABASE", "tickets_test")
    monkeypatch.setenv("MYSQL_PORT", os.getenv("MYSQL_TEST_PORT", "3308"))
