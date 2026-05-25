"""Minimal AgentScope demo that runs without an API key.

Run:
    source .venv/bin/activate
    python demo.py
"""

import asyncio

import agentscope
from agentscope.agent import AgentBase
from agentscope.message import Msg


class EchoAgent(AgentBase):
    """A tiny local agent for checking the AgentScope runtime."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.history: list[Msg] = []

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        if msg is None:
            return
        if isinstance(msg, list):
            self.history.extend(msg)
        else:
            self.history.append(msg)

    async def reply(self, msg: Msg | None = None) -> Msg:
        await self.observe(msg)

        user_text = msg.get_text_content() if msg else "empty input"
        response = Msg(
            name=self.name,
            role="assistant",
            content=(
                "AgentScope is running. "
                f"I received: {user_text!r}. "
                f"Messages observed so far: {len(self.history)}."
            ),
        )

        await self.print(response)
        return response


async def main() -> None:
    agentscope.init(project="AgentScopeStdDemo", name="local_echo_demo")

    agent = EchoAgent("local_echo_agent")
    user_msg = Msg(
        name="user",
        role="user",
        content="Hello AgentScope, please confirm the environment works.",
    )

    print("User message:")
    print(user_msg.to_dict())
    print()

    reply = await agent.reply(user_msg)

    print()
    print("Reply message:")
    print(reply.to_dict())


if __name__ == "__main__":
    asyncio.run(main())
