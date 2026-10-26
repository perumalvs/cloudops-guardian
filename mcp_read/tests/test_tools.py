import json, os, sys, pathlib
for k, v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test"}.items():
    os.environ[k] = v
os.environ.pop("AWS_PROFILE", None)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import main  # noqa: E402

HEADER = "    PID    PPID USER     COMMAND         %CPU %MEM"

def listing(n_procs, with_pid=None):
    rows = [f"{1000+i:7d}       1 root     proc{i:<10}  0.0  0.1" for i in range(n_procs)]
    if with_pid:
        rows.insert(0, f"{with_pid:7d}    1234 ec2-user yes           99.0  0.1")
    return "\n".join([HEADER] + rows)

class FakeSSM:
    class exceptions:
        class InvocationDoesNotExist(Exception): ...
    def __init__(self, out, status="Success", err=""):
        self.out, self.status, self.err = out, status, err
    def send_command(self, **kw):
        assert kw["DocumentName"] == "CloudOpsGuardian-GetTopCPUProcesses"
        assert "Parameters" not in kw
        return {"Command": {"CommandId": "c-1"}}
    def get_command_invocation(self, **kw):
        assert kw["PluginName"] == "getTopCPUProcesses"
        return {"Status": self.status, "StandardOutputContent": self.out, "StandardErrorContent": self.err}

def run(fn, ssm, *a):
    main.ssm = ssm
    return json.loads(fn(*a))

def test_top_cpu_is_limited_to_header_plus_10():
    r = run(main.get_top_cpu_processes, FakeSSM(listing(80, with_pid=4242)))
    assert r["status"] == "SUCCESS" and len(r["output"].splitlines()) == 11
    assert "4242" in r["output"]

def test_pid_found():
    r = run(main.get_process_details, FakeSSM(listing(80, with_pid=4242)), 4242)
    assert r["status"] == "FOUND" and "yes" in r["output"]

def test_pid_not_found_when_listing_complete():
    r = run(main.get_process_details, FakeSSM(listing(80)), 4242)
    assert r["status"] == "NOT_FOUND"

def test_head_limited_output_is_inconclusive_not_not_found():
    r = run(main.get_process_details, FakeSSM(listing(10)), 4242)
    assert r["status"] == "INCONCLUSIVE"

def test_truncated_output_is_inconclusive():
    r = run(main.get_process_details, FakeSSM(listing(80) + "\n" + "x" * 24000), 4242)
    assert r["status"] == "INCONCLUSIVE"

def test_pid_must_be_exact_not_prefix():
    r = run(main.get_process_details, FakeSSM(listing(80, with_pid=42421)), 4242)
    assert r["status"] == "NOT_FOUND"

def test_ssm_failure_is_not_not_found():
    r = run(main.get_process_details, FakeSSM("", status="Failed", err="boom"), 4242)
    assert r["status"] == "Failed" and r["error"] == "boom"

def test_invalid_pids_rejected():
    for bad in (0, -1, 4194305, True, "1; reboot"):
        try:
            main.get_process_details(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
