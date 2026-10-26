from cloudops_orchestrator.config import Config


def test_code_defaults_to_execution_off_when_the_variable_is_missing():
    assert Config.from_env({}).execution_enabled is False          # fail-safe: only the deploy script turns writes on


def test_execution_flag_parsing_is_strict():
    assert Config.from_env({"EXECUTION_ENABLED": "true"}).execution_enabled is True
    assert Config.from_env({"EXECUTION_ENABLED": "TRUE"}).execution_enabled is True
    for junk in ("false", "0", "yes", "", "on", "1"):
        assert Config.from_env({"EXECUTION_ENABLED": junk}).execution_enabled is False, junk


def test_operator_and_approver_lists_stay_separate_and_trimmed():
    c = Config.from_env({"CLOUDOPS_OPERATOR_EMAILS": " A@x.com , b@x.com ", "CLOUDOPS_APPROVER_EMAILS": "a@x.com"})
    assert c.operator_emails == ("a@x.com", "b@x.com") and c.approver_emails == ("a@x.com",)
