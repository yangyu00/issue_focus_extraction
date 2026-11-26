#!/usr/bin/env python3
"""入口：转发到 `xiaoxin/cli/app.py`（参数与调度）与 `xiaoxin/pipelines/issue_pipeline.py`（业务编排）。
"""
from xiaoxin.cli.app import main
from xiaoxin.pipelines.issue_pipeline import (  # noqa: F401
    build_labeled_input,
    parallel_process,
    process_model_output_for_db,
    run_batch_job,
    run_daily_job,
    run_process_for_date,
    safe_run_model,
)

if __name__ == "__main__":
    main()
