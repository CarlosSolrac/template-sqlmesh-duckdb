"""Unit tests of the console that sends SQLMesh's failure details to the project logger."""

import logging

import pytest
from sqlmesh.utils.concurrency import NodeExecutionFailedError

from project.diagnostics import FailureLog


def test_a_failed_model_that_is_not_an_audit_is_logged_with_its_error(caplog: pytest.LogCaptureFixture) -> None:
    failure: NodeExecutionFailedError[object] = NodeExecutionFailedError("orders.stg_orders")
    failure.__cause__ = ValueError("boom")

    FailureLog().log_failed_models([failure])

    assert [record.levelno for record in caplog.records] == [logging.ERROR]
    assert "orders.stg_orders" in caplog.text
    assert "boom" in caplog.text
