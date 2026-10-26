"""InvokeAgentRuntime wrapper for hackathon_cloudopsguardin_runtime_agent."""
import json

import boto3

from ..models import OUTCOMES

CONFIDENCE = ("LOW", "MEDIUM", "HIGH")
MODES = ("investigate", "followup", "query")


class ReasoningError(Exception):
    pass


def session_id(case_id):
    """Stable per-case runtime session (>= 33 chars required) so follow-ups keep context."""
    return f"cloudops-guardian-{case_id}-reasoning-session"


def validate_reasoning_response(data):
    """
    Normalize the agent's structured answer. The model is untrusted input:
    anything outside the allowed outcome catalog is dropped, never executed.
    Returns {diagnosis, evidence, outcomes, answer, dropped}.
    """
    if not isinstance(data, dict):
        raise ReasoningError("response is not an object")
    diagnosis = data.get("diagnosis") or {}
    if not isinstance(diagnosis, dict):
        raise ReasoningError("diagnosis must be an object")
    confidence = str(diagnosis.get("confidence", "LOW")).upper()
    outcomes, dropped = [], []
    for raw in data.get("outcomes") or []:
        capability = raw.get("capability") if isinstance(raw, dict) else None
        if capability not in OUTCOMES:
            dropped.append(str(capability)[:60])
            continue
        outcomes.append({
            "capability": capability,
            "rank": raw.get("rank") if isinstance(raw.get("rank"), int) else len(outcomes) + 1,
            "parameters": raw.get("parameters") if isinstance(raw.get("parameters"), dict) else {},
            "rationale": str(raw.get("rationale", ""))[:1000],
            "impact": str(raw.get("impact", ""))[:500],
            "risk": str(raw.get("risk", ""))[:500],
        })
    outcomes.sort(key=lambda o: o["rank"])
    if not outcomes:  # no suitable allowed capability -> escalate
        outcomes = [{"capability": "ESCALATE", "rank": 1, "parameters": {},
                     "rationale": "No suitable governed outcome was produced.", "impact": "", "risk": ""}]
    return {
        "diagnosis": {
            "summary": str(diagnosis.get("summary", ""))[:2000],
            "root_cause": str(diagnosis.get("root_cause", ""))[:1000],
            "confidence": confidence if confidence in CONFIDENCE else "LOW",
            "process_id": diagnosis.get("process_id") if isinstance(diagnosis.get("process_id"), int) else None,
            "process_name": str(diagnosis.get("process_name", ""))[:64] or None,
        },
        "evidence": [
            {"tool": str(e.get("tool", ""))[:100], "arguments": e.get("arguments") if isinstance(e.get("arguments"), dict) else {},
             "summary": str(e.get("summary", ""))[:2000], "data": e.get("data")}
            for e in (data.get("evidence") or []) if isinstance(e, dict)
        ],
        "outcomes": outcomes,
        "answer": str(data.get("answer", ""))[:6000],
        "dropped": dropped,
    }


class ReasoningRuntime:
    def __init__(self, runtime_arn, region="us-east-1", client=None):
        self.arn = runtime_arn
        self.client = client or boto3.client("bedrock-agentcore", region_name=region)

    def invoke(self, mode, case_id, **context):
        """context: case, question, known_evidence, ... The agent chooses READ tools itself."""
        if mode not in MODES:
            raise ReasoningError(f"unknown mode: {mode}")
        payload = json.dumps({"mode": mode, "case_id": case_id, **context}, default=str).encode()
        response = self.client.invoke_agent_runtime(
            agentRuntimeArn=self.arn, qualifier="DEFAULT",
            runtimeSessionId=session_id(case_id), payload=payload, contentType="application/json",
        )
        body = response["response"]
        raw = body.read() if hasattr(body, "read") else b"".join(body)
        try:
            return validate_reasoning_response(json.loads(raw))
        except ValueError as error:
            raise ReasoningError("reasoning runtime returned non-JSON") from error
