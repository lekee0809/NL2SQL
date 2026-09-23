import sys

import pytest

from eval import run_plan_eval


def test_cli_rejects_selection_above_api_call_cap(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_plan_eval.py",
            "--id", "advanced_001",
            "--id", "advanced_002",
            "--max-api-calls", "1",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        run_plan_eval.main()
    assert exc.value.code == 2
