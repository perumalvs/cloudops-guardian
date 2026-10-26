import os

from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent

MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-5-5")

app = BedrockAgentCoreApp()

agent = Agent(
    model=MODEL_ID
)


def _agent_factory(tools, system_prompt):
    inner = Agent(model=MODEL_ID, tools=tools, system_prompt=system_prompt)
    return lambda prompt: str(inner(prompt))


@app.entrypoint
def invoke(payload):
    """Reasoning entrypoint. Payloads with a 'mode' run the governed investigation;
    anything else keeps the original bootstrap behaviour."""

    if payload.get("mode") in ("investigate", "followup", "query"):
        from agent import run
        from gateway_client import ReadGatewayClient

        url = os.environ["READ_GATEWAY_MCP_URL"]
        tools = _read_tools(ReadGatewayClient(url))
        return run(payload, tools, _agent_factory)

    prompt = payload.get(
        "prompt",
        "Confirm that the CloudOps Guardian AgentCore runtime is operational."
    )

    result = agent(prompt)

    return {
        "result": result.message
    }


def _read_tools(client):
    from agent import ReadTools
    return ReadTools(client)


if __name__ == "__main__":
    app.run()
