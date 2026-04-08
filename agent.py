import os
from agents import Agent, Runner
from agents.items import MessageOutputItem
from dotenv import load_dotenv

load_dotenv()

agent = Agent(
    name="Assistant",
    instructions="You are a helpful assistant.",
    model="gpt-4o-mini",
)


async def run_agent(history: list[dict]) -> tuple[str, str | None]:
    """
    Run the agent with the full conversation history.

    Args:
        history: list of {"role": "user"|"assistant", "content": "..."}

    Returns:
        (reply_text, detected_intent)
    """
    result = await Runner.run(agent, history)
    reply = result.final_output

    # Best-effort intent detection from last user message
    intent = None
    last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    lower = last_user.lower()
    if any(w in lower for w in ("hello", "hi", "hey", "start")):
        intent = "greeting"
    elif "?" in lower:
        intent = "question"
    elif any(w in lower for w in ("help", "support", "problem", "issue")):
        intent = "support_request"
    elif any(w in lower for w in ("complain", "complaint", "unhappy", "bad")):
        intent = "complaint"
    else:
        intent = "general"

    return reply, intent
