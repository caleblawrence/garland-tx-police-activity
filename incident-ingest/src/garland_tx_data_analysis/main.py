#!/usr/bin/env python
"""Run the weekly-report deep agent."""

import warnings

from dotenv import load_dotenv

# Override any stale/empty values set in the parent shell with what's in .env.
# Without override=True, an exported-but-empty ANTHROPIC_API_KEY in the user's
# shell silently masks the value in .env and the run fails to authenticate.
load_dotenv(override=True)

from garland_tx_data_analysis.agent import build_agent  # noqa: E402

warnings.filterwarnings("ignore", category=SyntaxWarning)

KICKOFF = "Publish this week's Garland incident report."

# Each tool call, subagent hop and audit round is a step. A clean run is well
# under this; the limit is here so a stuck loop fails instead of billing.
RECURSION_LIMIT = 150


def _text_of(message) -> str:
    """Message content as plain text.

    Anthropic responses arrive as a list of content blocks, not a string.
    """
    content = message.content
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return str(content).strip()


def _describe(message) -> str:
    """One line per message, so a run is readable as it happens."""
    kind = message.__class__.__name__.replace("Message", "").lower()
    calls = getattr(message, "tool_calls", None)
    if calls:
        return "  ".join(f"[{kind} -> {c['name']}]" for c in calls)
    name = getattr(message, "name", None)
    label = f"{kind}:{name}" if name else kind
    return f"[{label}] {' '.join(_text_of(message).split())[:400]}"


def run() -> None:
    """Run the agent, streaming its progress."""
    agent = build_agent()
    seen = 0
    final = None

    for chunk in agent.stream(
        {"messages": [{"role": "user", "content": KICKOFF}]},
        config={"recursion_limit": RECURSION_LIMIT},
        stream_mode="values",
    ):
        messages = chunk.get("messages", [])
        for message in messages[seen:]:
            print(_describe(message), flush=True)
        seen = len(messages)
        final = messages[-1] if messages else final

    if final is not None:
        print("\n=== Result ===")
        print(_text_of(final))


if __name__ == "__main__":
    run()
