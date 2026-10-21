"""Unit tests for script utilities and checks."""

from __future__ import annotations

import pytest
from scripts import check_tool_contract


def test_check_tool_contract_main(capsys: pytest.CaptureFixture[str]) -> None:
    code = check_tool_contract.main()
    assert code == 0
    captured = capsys.readouterr()
    assert "README count validation:" in captured.out
    assert "All tool-contract assertions passed." in captured.out
