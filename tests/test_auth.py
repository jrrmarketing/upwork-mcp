from upwork_mcp.browser.auth import HEYLOGIN_LOGIN_STEPS


def test_login_instructions_use_separate_heylogin_vault_tab() -> None:
    instructions = "\n".join(HEYLOGIN_LOGIN_STEPS).lower()

    assert "https://heylogin.app/" in instructions
    assert "separate chrome tab" in instructions
    assert "upwork.com" in instructions
    assert "intended freelancer identity" in instructions
    assert "exact matched entry" in instructions
    assert "extension" not in instructions
