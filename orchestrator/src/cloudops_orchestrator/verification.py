"""Fresh READ verification (process, CPU, EC2, alarm); RESOLVED only if recovery verified."""
HOT_CPU = 50.0                       # ps %cpu is per-core, so 50 is half a core
IGNORED = ("ssm-document-wo", "ssm-session-wo", "amazon-ssm-agen", "ssm-agent", "ps")   # the diagnostics themselves
REQUIRED = {"terminate_process": ("process_gone", "instance_ok", "alarm_ok", "cpu_ok"),
            "reboot_demo_instance": ("instance_ok", "alarm_ok", "cpu_ok")}


def parse_rows(output):
    """Rows of `ps -eo pid,ppid,user,comm,%cpu,%mem`."""
    rows = []
    for line in str(output or "").splitlines()[1:]:
        f = line.split()
        if len(f) >= 6 and f[0].isdigit():
            try:
                rows.append({"pid": int(f[0]), "comm": f[3], "cpu": float(f[-2])})
            except ValueError:
                continue
    return rows


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 - a failed READ is "unknown", never "fine"
        return None


def process_state(details, name):
    """GONE / RUNNING / UNKNOWN for one pid; a pid reused by another program counts as the original being gone."""
    status = (details or {}).get("status")
    if status == "NOT_FOUND":
        return "GONE", None
    if status == "FOUND":
        rows = parse_rows(details.get("output"))
        if rows and rows[0]["comm"] == name:
            return "RUNNING", rows[0]
        return "GONE", rows[0] if rows else None
    return "UNKNOWN", None


def collect(read, plan):
    """One fresh round of READ checks. Each value is True, False, or None (unknown)."""
    status, alarm, cpu = _safe(read.instance_status), _safe(read.alarm), _safe(read.cpu_metrics, 1)
    checks = {"instance_ok": None, "alarm_ok": None, "cpu_ok": None}
    detail = {"alarm": (alarm or {}).get("stateValue"), "instance": (status or {}).get("instanceState"),
              "system": (status or {}).get("systemStatus"), "instance_status": (status or {}).get("instanceStatus")}
    if status:
        checks["instance_ok"] = (status.get("instanceState"), status.get("systemStatus"), status.get("instanceStatus")) == ("running", "ok", "ok")
    if alarm and alarm.get("stateValue") in ("OK", "ALARM"):
        checks["alarm_ok"] = alarm["stateValue"] == "OK"
    points = sorted((cpu or {}).get("datapoints") or [], key=lambda p: str(p.get("Timestamp")))
    if points:
        threshold = (alarm or {}).get("threshold") or 80.0
        detail["cpu_latest"] = round(points[-1].get("Average", 0), 1)
        checks["cpu_ok"] = points[-1].get("Average", 0) < threshold
    if plan["capability"] == "terminate_process":
        state, _ = process_state(_safe(read.process_details, plan["parameters"]["process_id"]), plan["parameters"]["expected_process_name"])
        checks["process_gone"] = None if state == "UNKNOWN" else state == "GONE"
        detail["process"] = state
    tops = _safe(read.top_processes)
    detail["residual_hot"] = [f"{r['comm']}(pid {r['pid']}) {r['cpu']}%" for r in parse_rows((tops or {}).get("output"))
                              if r["cpu"] >= HOT_CPU and r["comm"] not in IGNORED]
    return checks, detail


def evaluate(plan, checks):
    """(passed, blocking): passed only when every required check is exactly True."""
    required = REQUIRED[plan["capability"]]
    blocking = [name for name in required if checks.get(name) is not True]
    return not blocking, blocking


def describe(checks, detail):
    def mark(v):
        return {True: "yes", False: "NO", None: "unknown"}[v]
    lines = []
    if "process_gone" in checks:
        lines.append(f"process gone: {mark(checks['process_gone'])}")
    lines += [f"instance running with ok status checks: {mark(checks['instance_ok'])}",
              f"CPU alarm back to OK: {mark(checks['alarm_ok'])} (alarm is {detail.get('alarm')})",
              f"CPU below threshold: {mark(checks['cpu_ok'])} (latest {detail.get('cpu_latest', 'n/a')}%)"]
    if detail.get("residual_hot"):
        lines.append("note - still busy: " + ", ".join(detail["residual_hot"]))
    return "; ".join(lines)
