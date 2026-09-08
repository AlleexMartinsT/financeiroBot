def run_with_recovery(
    execute_loop,
    should_stop,
    wait_for_retry,
    report_failure,
    retry_seconds: int = 60,
):
    while not should_stop():
        try:
            execute_loop()
        except Exception as exc:
            report_failure(exc)
            if wait_for_retry(retry_seconds):
                break
        else:
            break
