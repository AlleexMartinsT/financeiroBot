import unittest
from unittest.mock import Mock

from loop_supervisor import run_with_recovery


class MainLoopRecoveryTests(unittest.TestCase):
    def test_keeps_the_worker_alive_after_an_unexpected_cycle_failure(self):
        execute_loop = Mock(side_effect=RuntimeError("falha inesperada"))
        report_failure = Mock()
        wait_for_retry = Mock(return_value=True)

        run_with_recovery(
            execute_loop=execute_loop,
            should_stop=lambda: False,
            wait_for_retry=wait_for_retry,
            report_failure=report_failure,
            retry_seconds=60,
        )

        execute_loop.assert_called_once()
        report_failure.assert_called_once()
        self.assertEqual("falha inesperada", str(report_failure.call_args.args[0]))
        wait_for_retry.assert_called_once_with(60)
