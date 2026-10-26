"""Deterministic, model-free evidence handling: summaries and grounding of proposed outcomes."""
import json

MAX_DATA_CHARS = 6000


def parse_rows(output):
    """Rows of `ps -eo pid,ppid,user,comm,%cpu,%mem`: list of dicts (header skipped)."""
    rows = []
    for line in str(output or "").splitlines()[1:]:
        f = line.split()
        if len(f) >= 6 and f[0].isdigit():
            try:
                rows.append({"pid": int(f[0]), "comm": f[3], "cpu": float(f[-2]), "mem": float(f[-1])})
            except ValueError:
                continue
    return rows


def summarize(tool, data):
    """One short factual line per tool result."""
    if not isinstance(data, dict):
        return str(data)[:200]
    if tool == "get_top_cpu_processes":
        rows = parse_rows(data.get("output"))[:3]
        return "top CPU: " + ", ".join(f"{r['comm']}(pid {r['pid']}) {r['cpu']}%" for r in rows) if rows else f"status {data.get('status')}"
    if tool == "get_process_details":
        return f"pid {data.get('processId')}: {data.get('status')}"
    if tool == "get_demo_instance_cpu_metrics":
        pts = data.get("datapoints") or []
        if not pts:
            return "no CPU datapoints"
        return f"{len(pts)} datapoints; latest avg {pts[-1].get('Average')}%, max in window {max(p.get('Maximum', 0) for p in pts)}%"
    if tool == "describe_cpu_alarm":
        return f"alarm {data.get('stateValue')} (threshold {data.get('threshold')}): {str(data.get('stateReason', ''))[:120]}"
    if tool == "get_demo_instance_status":
        return f"instance {data.get('instanceState')}, system {data.get('systemStatus')}, instance-status {data.get('instanceStatus')}"
    if tool == "describe_demo_instance":
        return f"{data.get('instanceId')} {data.get('state')} {data.get('instanceType')}"
    if tool == "get_cpu_alarm_history":
        return f"{len(data.get('history') or [])} history records"
    return json.dumps(data, default=str)[:200]


def truncate(text, n=MAX_DATA_CHARS):
    return text if len(text) <= n else text[:n] + "...[truncated]"


def process_seen(calls, pid, name):
    """True only if a READ result in THIS invocation shows this exact pid running as this name."""
    for c in calls:
        data = c.get("data")
        if c["tool"] == "get_top_cpu_processes" and isinstance(data, dict):
            if any(r["pid"] == pid and r["comm"] == name for r in parse_rows(data.get("output"))):
                return True
        if c["tool"] == "get_process_details" and isinstance(data, dict) and data.get("status") == "FOUND":
            if data.get("processId") == pid and name in str(data.get("output", "")).split():
                return True
    return False


def ground_outcomes(outcomes, calls):
    """
    A terminate_process proposal must name a pid/process that the evidence
    gathered in this run actually shows; otherwise it is dropped (never trusted
    from model text alone). Returns (outcomes, notes).
    """
    kept, notes = [], []
    for o in outcomes:
        if o.get("capability") == "terminate_process":
            p = o.get("parameters") or {}
            pid, name = p.get("process_id"), p.get("expected_process_name")
            if isinstance(pid, bool) or not isinstance(pid, int) or not isinstance(name, str) or not process_seen(calls, pid, name):
                notes.append("terminate_process dropped: pid/name not confirmed by evidence gathered in this run")
                continue
            o = {**o, "parameters": {"process_id": pid, "expected_process_name": name}}
        elif o.get("capability") == "reboot_demo_instance":
            o = {**o, "parameters": {}}
        kept.append(o)
    return kept, notes
