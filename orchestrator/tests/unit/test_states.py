import pytest
from cloudops_orchestrator.states import CaseState as S, can_transition, normalize, sources


def test_open_is_detected():
    assert normalize("OPEN") is S.DETECTED


def test_happy_path_is_legal():
    path = [S.DETECTED, S.INVESTIGATING, S.DIAGNOSED, S.AWAITING_APPROVAL, S.APPROVED,
            S.EXECUTING, S.VERIFYING, S.RESOLVED]
    assert all(can_transition(a, b) for a, b in zip(path, path[1:]))


@pytest.mark.parametrize("a,b", [
    (S.EXECUTING, S.RESOLVED),          # API success is never resolution
    (S.DIAGNOSED, S.EXECUTING),         # cannot skip approval
    (S.AWAITING_APPROVAL, S.EXECUTING),
    (S.AWAITING_USER, S.APPROVED),
    (S.RESOLVED, S.INVESTIGATING),
    (S.DETECTED, S.RESOLVED),
])
def test_illegal(a, b):
    assert not can_transition(a, b)


def test_only_verifying_reaches_resolved():
    assert sources(S.RESOLVED) == {"VERIFYING"}


def test_only_approved_reaches_executing():
    assert sources(S.EXECUTING) == {"APPROVED"}


def test_open_counts_as_a_detected_source():
    assert "OPEN" in sources(S.INVESTIGATING)
