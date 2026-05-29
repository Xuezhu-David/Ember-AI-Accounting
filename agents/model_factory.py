"""Centralized model creation for all AgentScope agents."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

from agentscope.credential import AnthropicCredential
from agentscope.formatter import AnthropicChatFormatter
from agentscope.model import AnthropicChatModel

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL") or None
ANTHROPIC_MODEL_NAME = os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL") or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
ANTHROPIC_VISION_MODEL_NAME = ANTHROPIC_MODEL_NAME


def create_chat_model(vision: bool = False) -> AnthropicChatModel:
    """Create an AnthropicChatModel using the configured API key."""
    model_name = ANTHROPIC_VISION_MODEL_NAME if vision else ANTHROPIC_MODEL_NAME
    return AnthropicChatModel(
        credential=AnthropicCredential(api_key=ANTHROPIC_API_KEY, base_url=ANTHROPIC_BASE_URL),
        model=model_name,
        stream=True,
        parameters=AnthropicChatModel.Parameters(temperature=0.1),
    )
