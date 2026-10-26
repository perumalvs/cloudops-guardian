import pytest
from cloudops_orchestrator.models import make_plan_id, parse_plan_id, params_hash, canonical_json


def test_plan_id_roundtrip():
    pid = make_plan_id("CG-52650C02BEB8", 2)
    assert pid == "PLAN-52650C02BEB8V2"
    assert parse_plan_id(pid) == ("CG-52650C02BEB8", 2)
    assert parse_plan_id(pid.lower()) == ("CG-52650C02BEB8", 2)


@pytest.mark.parametrize("bad", ["", "PLAN-1", "PLAN-52650C02BEB8", "PLAN-52650C02BEB8V0", "PLAN-ZZZZZZZZZZZZV1", None])
def test_bad_plan_ids(bad):
    with pytest.raises(ValueError):
        parse_plan_id(bad)


def test_params_hash_is_order_independent_and_sensitive():
    a = {"process_id": 4242, "expected_process_name": "yes"}
    assert params_hash(a) == params_hash(dict(reversed(list(a.items()))))
    assert params_hash(a) != params_hash({**a, "process_id": 4243})
    assert canonical_json(a) == '{"expected_process_name":"yes","process_id":4242}'
