"""Failure details from SQLMesh, sent to the project logger so a failed run names the audit or test that failed."""

import logging

from sqlmesh.core.console import NoopConsole
from sqlmesh.core.test.result import ModelTextTestResult
from sqlmesh.utils.concurrency import NodeExecutionFailedError
from sqlmesh.utils.errors import AuditError, NodeAuditsErrors

logger: logging.Logger = logging.getLogger(__name__)


class FailureLog(NoopConsole):
    """A SQLMesh console that is silent except for failures: each failed audit, failed model and failed model test is logged as an error."""

    def log_failed_models(self, errors: list[NodeExecutionFailedError[object]]) -> None:
        """Log each audit that failed, or the error of each model that failed for another reason."""
        error: NodeExecutionFailedError[object]
        audit_error: AuditError
        for error in errors:
            cause: BaseException = error.__cause__ or error
            if isinstance(cause, NodeAuditsErrors):
                for audit_error in cause.errors:
                    logger.error("%s: %s", audit_error.model_name or error.node, audit_error)
            else:
                logger.error("%s: %s", error.node, cause)

    def log_test_results(self, result: ModelTextTestResult, target_dialect: str) -> None:
        """Log each model test that failed or errored."""
        test: object
        for test in result.get_fail_and_error_tests():
            logger.error("Model test failed on %s: %s", target_dialect, test)
