import pytest
from cloudops_orchestrator.tokens import generate_token, hash_token, verify_token


def test_tokens_unique_and_whitespace_free():
    a, b = generate_token(), generate_token()
    assert a != b and len(a) >= 40 and not any(c.isspace() for c in a)


def test_hash_bound_to_plan_and_pepper():
    h = hash_token("t", "PLAN-1V1", "p")
    assert h != hash_token("t", "PLAN-2V1", "p") and h != hash_token("t", "PLAN-1V1", "q")
    assert verify_token("t", "PLAN-1V1", "p", h)
    assert not verify_token("T", "PLAN-1V1", "p", h)
    assert not verify_token("", "PLAN-1V1", "p", h) and not verify_token("t", "PLAN-1V1", "p", None)


def test_pepper_required():
    with pytest.raises(ValueError):
        hash_token("t", "PLAN-1V1", "")
