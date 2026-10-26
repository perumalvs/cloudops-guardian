"""Generate docs/architecture.svg for CloudOps Guardian."""
import sys
from xml.sax.saxutils import escape

W, H = 1500, 940
C = {  # fill, stroke
    "aws": ("#fff7ed", "#c2410c"),     # CDS / SES (hero)
    "agent": ("#f3e8ff", "#7e22ce"),   # AgentCore / Bedrock
    "flow": ("#fce7f3", "#be185d"),    # Step Functions / EventBridge
    "fn": ("#e0f2fe", "#0369a1"),      # Lambda
    "data": ("#dcfce7", "#15803d"),    # DynamoDB / S3
    "res": ("#f1f5f9", "#475569"),     # AWS resources, people
    "write": ("#fee2e2", "#b91c1c"),   # governed write path
}
out = []


def box(x, y, w, h, title, sub="", kind="fn", extra=""):
    f, s = C[kind]
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{f}" stroke="{s}" stroke-width="1.6" {extra}/>')
    ty = y + 22 if (sub or h > 100) else y + h / 2 + 5
    fs = min(14, (w - 16) / (len(title) * 0.62))
    out.append(f'<text x="{x + w / 2}" y="{ty}" text-anchor="middle" font-weight="700" font-size="{fs:.1f}" fill="#111827">{escape(title)}</text>')
    for i, line in enumerate(sub.split("\n") if sub else []):
        out.append(f'<text x="{x + w / 2}" y="{y + 40 + i * 15}" text-anchor="middle" font-size="11.5" fill="#374151">{escape(line)}</text>')


def arrow(pts, color="#374151", dashed=False, both=False, label=None, lx=0, ly=0, marker="a"):
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    start = f' marker-start="url(#{marker})"' if both else ""
    out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.8"{dash} marker-end="url(#{marker})"{start}/>')
    if label:
        out.append(f'<text x="{lx}" y="{ly}" font-size="11" fill="{color}" font-style="italic">{escape(label)}</text>')


def badge(x, y, n, color="#111827"):
    out.append(f'<circle cx="{x}" cy="{y}" r="11" fill="{color}"/><text x="{x}" y="{y + 4}" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">{n}</text>')


out.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="Segoe UI, Roboto, Helvetica, Arial, sans-serif">')
out.append('<defs>'
           '<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>'
           '<marker id="o" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#c2410c"/></marker>'
           '<marker id="r" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#b91c1c"/></marker>'
           '</defs>')
out.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
out.append('<text x="40" y="38" font-size="24" font-weight="800" fill="#111827">CloudOps Guardian: conversational incident response over email (Amazon SES + Bedrock AgentCore)</text>')
out.append('<text x="40" y="60" font-size="13" fill="#4b5563">An agent investigates an EC2 incident with governed read-only tools, then holds the conversation with the operator by email until the fix is approved, executed and verified.</text>')

# Column headings
for x, t in ((40, "DETECT"), (330, "ORCHESTRATE"), (700, "INVESTIGATE (read-only)"), (1060, "ACT (governed write)")):
    out.append(f'<text x="{x}" y="86" font-size="11" font-weight="700" letter-spacing="1.5" fill="#6b7280">{t}</text>')

# Detect column
box(40, 100, 190, 60, "CloudWatch Alarm", "EC2 CPU > 80% (5 min avg)", "res")
box(40, 200, 190, 56, "Amazon EventBridge", "alarm state-change rule", "flow")
box(40, 296, 190, 66, "Step Functions", "IncidentWorkflow\ncreate case, then investigate", "flow")
box(40, 400, 190, 60, "IncidentHandler", "Lambda: opens case CG-…", "fn")
arrow([(135, 160), (135, 200)]); arrow([(135, 256), (135, 296)]); arrow([(135, 362), (135, 400)])
badge(150, 180, 1)

# Orchestrator
box(330, 250, 260, 330, "Orchestrator (Lambda)", "", "fn")
for i, t in enumerate(["Case state machine (14 states)", "Plan builder: immutable, parameter-hashed", "Approval validator: sender allow-list,",
                       "  single-use token, SES auth verdicts", "Execute-once claim (idempotent)", "Pre-action re-check of live state",
                       "Audit trail with secret redaction", "Kill switch: EXECUTION_ENABLED"]):
    out.append(f'<text x="345" y="{310 + i * 22}" font-size="11.5" fill="#374151">{escape(t)}</text>')
arrow([(230, 329), (330, 329)], label="investigate", lx=244, ly=322)

# DynamoDB + dashboard
box(330, 640, 260, 64, "DynamoDB: CaseState", "single table: case, plans, evidence,\napprovals, executions, audit", "data")
arrow([(460, 580), (460, 640)], both=True)
arrow([(230, 430), (300, 430), (300, 662), (330, 662)], label="create case", lx=236, ly=424)
box(40, 650, 190, 56, "Dashboard (Lambda)", "read-only, masked", "fn")
arrow([(230, 686), (330, 686)], label="query", lx=262, ly=680)
box(40, 760, 190, 50, "Judges / operators", "browser", "res")
arrow([(135, 760), (135, 706)])

# Investigate column
box(700, 100, 240, 72, "AgentCore Runtime (reasoning)", "Bedrock model picks READ tools,\nreturns diagnosis + ranked options", "agent")
box(700, 205, 240, 56, "AgentCore Gateway (READ)", "IAM-authenticated MCP", "agent")
box(700, 290, 240, 56, "READ MCP Runtime", "7 tools: instance, CPU, alarm, processes", "agent")
box(700, 375, 240, 56, "EC2 · CloudWatch · SSM", "read-only diagnostics", "res")
arrow([(500, 250), (500, 136), (700, 136)], label="investigate", lx=530, ly=128)
arrow([(820, 172), (820, 205)]); arrow([(820, 261), (820, 290)]); arrow([(820, 346), (820, 375)])
badge(500, 190, 2)

# Act column
box(1050, 205, 260, 76, "AgentCore Gateway (ACTION)", "Cedar permits only terminate_process\nand reboot_demo_instance", "write")
box(1060, 310, 240, 56, "ACTION MCP Runtime", "plan-bound, tag-gated tools", "write")
box(1060, 395, 240, 56, "SSM Run Command · EC2", "tagged demo instance only", "res")
arrow([(540, 250), (540, 189), (1180, 189), (1180, 205)], color="#b91c1c", marker="r", label="execute, only after approval + re-check", lx=960, ly=183)
arrow([(1180, 281), (1180, 310)], color="#b91c1c", marker="r"); arrow([(1180, 366), (1180, 395)], color="#b91c1c", marker="r")
badge(640, 189, 6, "#b91c1c")
box(1330, 205, 130, 146, "Verification", "Step Functions loop:\nwait, fresh READ,\nre-check alarm.\nRecovery proven\nthen RESOLVED", "flow")
arrow([(1300, 423), (1395, 423), (1395, 351)], dashed=True)
badge(1395, 395, 7, "#be185d")

# CDS band
out.append('<rect x="660" y="600" width="800" height="270" rx="14" fill="#fff7ed" stroke="#c2410c" stroke-width="2" stroke-dasharray="8 5"/>')
out.append('<text x="680" y="626" font-size="13" font-weight="800" fill="#c2410c" letter-spacing="1">AWS COMMUNICATION DEVELOPER SERVICES: THE CONVERSATION CHANNEL</text>')
box(700, 650, 230, 76, "Amazon SES (outbound)", "diagnosis, plan, and how to reply:\nDETAILS · approve · reject ·\nPROPOSE n · ESCALATE", "aws")
box(1260, 650, 170, 76, "Operator mailbox", "reads plan, replies in\nplain language", "res")
arrow([(930, 688), (1260, 688)], color="#c2410c", marker="o", label="plan email", lx=1040, ly=680)
box(1050, 770, 200, 76, "Amazon SES (inbound)", "receipt rule: spam, virus, SPF/\nDKIM/DMARC verdicts kept", "aws")
box(850, 770, 150, 76, "S3", "raw inbound mail", "data")
box(690, 770, 130, 76, "Inbound parser", "Lambda: classify\nintent, extract ids", "fn")
arrow([(1345, 726), (1345, 808), (1250, 808)], color="#c2410c", marker="o", label="reply", lx=1356, ly=784)
arrow([(1050, 808), (1000, 808)], color="#c2410c", marker="o")
arrow([(850, 808), (820, 808)])
badge(1345, 760, 4, "#c2410c")
badge(645, 560, 3, "#c2410c")
# Orchestrator <-> comms corridors
arrow([(590, 480), (645, 480), (645, 688), (700, 688)], color="#c2410c", marker="o")
arrow([(690, 808), (612, 808), (612, 540), (590, 540)], color="#c2410c", marker="o")
badge(612, 610, 5, "#c2410c")
# Legend
out_legend = []
for i, (k, t) in enumerate((("aws", "CDS (SES)"), ("agent", "Bedrock AgentCore"), ("flow", "Step Functions / EventBridge"),
                            ("fn", "Lambda"), ("data", "DynamoDB / S3"), ("write", "Governed write path"))):
    f, s = C[k]
    col, row = divmod(i, 3)
    x, y = 255 + col * 190, 770 + row * 20
    out_legend.append(f'<rect x="{x}" y="{y}" width="14" height="12" rx="3" fill="{f}" stroke="{s}" stroke-width="1.4"/>'
                      f'<text x="{x + 20}" y="{y + 10}" font-size="11" fill="#374151">{escape(t)}</text>')
out.extend(out_legend)
out.append('<text x="40" y="902" font-size="11" fill="#6b7280">Numbered steps: 1 alarm · 2 investigate · 3 plan email · 4 operator reply · 5 validate approval · 6 execute · 7 verify</text>')
out.append('<text x="40" y="922" font-size="11" fill="#6b7280">Writes happen only after: allow-listed approver, single-use token, live re-check, Cedar permit. Verification, not the API result, decides RESOLVED.</text>')
out.append('</svg>')
open(sys.argv[1], "w").write("\n".join(out))
