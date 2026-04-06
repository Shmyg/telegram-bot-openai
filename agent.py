import os
from agents import Agent, Runner
from dotenv import load_dotenv

load_dotenv()

agent = Agent(
    name="Assistant",
    instructions="You are a helpful assistant.",
    model="gpt-4o-mini",
)


async def run_agent(message: str) -> str:
    result = await Runner.run(agent, message)
    return result.final_output
