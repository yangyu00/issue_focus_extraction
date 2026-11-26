#!/usr/bin/env python3
"""入口：转发到 `xiaoxin/cli/generate_qa.py` 与 `xiaoxin/pipelines/qa_pipeline.py`。
"""
from xiaoxin.cli.generate_qa import main
from xiaoxin.pipelines.qa_pipeline import (  # noqa: F401
    call_qa_model,
    enrich_qa_excel,
    process_non_vendor_data,
)

if __name__ == "__main__":
    main()
