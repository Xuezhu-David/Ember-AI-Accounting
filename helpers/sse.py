"""SSE (Server-Sent Events) utility functions."""

import json


def _sse(data: dict) -> bytes:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


def _extract_reply_delta(accumulated: str, last_len: int) -> str:
    """Extract the new portion of the reply field from partially-accumulated JSON.

    Tries to parse the accumulated text as complete JSON first, then falls back
    to completing the string with a closing quote and brace so partial JSON can
    be decoded correctly — handling all standard JSON escape sequences.
    """
    for candidate in (accumulated, accumulated + '"}\n}', accumulated + '"}'):
        try:
            data = json.loads(candidate)
            reply = data.get("reply", "")
            if len(reply) > last_len:
                return reply[last_len:]
            return ""
        except (json.JSONDecodeError, AttributeError):
            continue
    return ""
