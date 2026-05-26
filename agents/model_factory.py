"""Centralized model creation for all AgentScope agents."""

import os

from agentscope.formatter import OpenAIChatFormatter
from agentscope.model import OpenAIChatModel

PMDE_BASE_URL = os.environ.get(
    "PMDE_BASE_URL",
    "https://ark.cn-beijing.volces.com/api/coding/v3",
)
PMDE_API_KEY = os.environ.get(
    "PMDE_API_KEY",
    "4fea2171-9079-434e-bdf5-d98a00db9363",
)
PMDE_MODEL_NAME = os.environ.get("PMDE_MODEL_NAME", "deepseek-v4-pro")
PMDE_VISION_MODEL_NAME = os.environ.get("PMDE_VISION_MODEL_NAME", "deepseek-v4-pro")


def create_chat_model(vision: bool = False) -> OpenAIChatModel:
    """Create an OpenAIChatModel for the configured DeepSeek endpoint."""
    return OpenAIChatModel(
        model_name=PMDE_VISION_MODEL_NAME if vision else PMDE_MODEL_NAME,
        api_key=PMDE_API_KEY,
        stream=False,
        client_kwargs={"base_url": PMDE_BASE_URL},
        generate_kwargs={"temperature": 0.1},
    )


def create_formatter() -> OpenAIChatFormatter:
    """Create the default OpenAI chat formatter."""
    return OpenAIChatFormatter()
