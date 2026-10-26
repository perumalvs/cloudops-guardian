# Deployment map (one component per checkpoint)

| Path | Deploys to |
|---|---|
| orchestrator/src/** | New Lambda CloudOpsGuardian-Orchestrator |
| deploy/stepfunctions/ | Step Functions IncidentWorkflow (additive state after case creation) |
| reasoning/ | Existing reasoning Runtime only (additive) |
| mcp_read/ (optional get_process_details) | READ ZIP -> READ Runtime -> sync target |
| ACTION / Cedar / parser | FROZEN — no deployment |
| dashboard/ | New Lambda CloudOpsGuardian-Dashboard + public Function URL (read-only; deploy/deploy-dashboard.sh) |
| baseline/ | none (reference) |

MCP packaging gates: Python 3.12, aarch64-manylinux2014, mcp<2, fastmcp dir present, .so AArch64, root main.py, keep rollback ZIP.
