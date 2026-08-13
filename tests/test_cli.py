"""Offline tests for command-line health semantics."""

import sys

import pytest

from upwork_mcp import server


@pytest.mark.parametrize(("valid", "expected_code"), [(True, 0), (False, 1)])
def test_check_exit_code_matches_session_state(
    monkeypatch: pytest.MonkeyPatch,
    valid: bool,
    expected_code: int,
) -> None:
    async def fake_check_session() -> bool:
        return valid

    monkeypatch.setattr(sys, "argv", ["upwork-mcp", "--check"])
    monkeypatch.setattr(server, "check_session", fake_check_session)

    with pytest.raises(SystemExit) as raised:
        server.main()

    assert raised.value.code == expected_code
