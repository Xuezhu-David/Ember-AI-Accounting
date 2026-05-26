"""Intent recognition agent — classifies user intent and extracts structured data."""

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import date
from decimal import Decimal

from agentscope.event import (
    EventBase,
    ReplyStartEvent,
    ReplyEndEvent,
    TextBlockStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
)
from agentscope.message import Msg, UserMsg, SystemMsg, AssistantMsg

from prompts import NL_PARSE_SYSTEM_PROMPT
from voucher_models import SalesTransaction

from .agent_config import IDENTITY_CONTEXT, AGENT_NAME, AGENT_CAPABILITIES
from .model_factory import create_chat_model

logger = logging.getLogger(__name__)


class IntentAgent:
    """Classify user intent and extract business data from natural language."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.model = create_chat_model()

    async def reply(self, msg: Msg) -> Msg:
        final = None
        async for event in self.reply_stream(msg, session_id=""):
            if isinstance(event, ReplyStartEvent):
                final = AssistantMsg(name=self.name, content=[], id=event.reply_id)
            if final is not None:
                final.append_event(event)
        return final  # type: ignore[return-value]

    async def reply_stream(self, msg: Msg, session_id: str = "") -> AsyncGenerator[EventBase, None]:
        message = msg.get_text_content() or ""
        conversation_history = msg.metadata.get("history", []) if msg.metadata else []

        today = date.today().strftime("%Y-%m-%d")
        user_prompt = (
            f"当前日期：{today}\n\n用户输入：{message}\n\n"
            "请先判断用户意图（intent），再进行后续处理。"
        )

        system_prompt = NL_PARSE_SYSTEM_PROMPT + IDENTITY_CONTEXT
        messages: list[Msg] = [SystemMsg(name="system", content=system_prompt)]
        for hist_msg in conversation_history[-200:]:
            role = hist_msg.get("role", "user")
            content = hist_msg.get("content", "")
            if role == "assistant":
                messages.append(AssistantMsg(name="assistant", content=content))
            else:
                messages.append(UserMsg(name="user", content=content))
        messages.append(UserMsg(name="user", content=user_prompt))

        msg_id = str(uuid.uuid4())
        yield ReplyStartEvent(reply_id=msg_id, session_id=session_id, name=self.name)

        try:
            response = await self.model(messages)
            result_msg = AssistantMsg(name=self.name, content=list(response.content), id=msg_id)
            raw = result_msg.get_text_content() or ""
            logger.info("IntentAgent raw response: %s", raw[:300])
            parse_result = self._parse_response(raw, today)
        except Exception as exc:
            logger.error("IntentAgent LLM call failed: %s", exc)
            parse_result = None

        if parse_result is None:
            parse_result = {
                "intent": "chat",
                "reply": f"你好！我是 {AGENT_NAME}，{AGENT_CAPABILITIES.replace('、', '、')}。有什么可以帮你的吗？",
                "business_type": None,
                "transaction": None,
            }

        text = json.dumps(parse_result, ensure_ascii=False, default=str)
        result_msg = AssistantMsg(
            name=self.name,
            content=text,
            id=msg_id,
            metadata={"parse_result": parse_result},
        )

        block_id = str(uuid.uuid4())
        yield TextBlockStartEvent(reply_id=msg_id, block_id=block_id)
        yield TextBlockDeltaEvent(reply_id=msg_id, block_id=block_id, delta=text)
        yield TextBlockEndEvent(reply_id=msg_id, block_id=block_id)
        yield ReplyEndEvent(reply_id=msg_id, session_id=session_id)

    def _parse_response(self, raw: str, today: str) -> dict | None:
        """Parse LLM response JSON into a structured result."""
        json_str = raw.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM response as JSON: %s", raw[:200])
            return None

        intent = data.get("intent", "unknown")

        if intent == "chat":
            return {
                "intent": "chat",
                "reply": data.get("reply", "你好！我是 Ember，有什么可以帮你的吗？"),
                "business_type": None,
                "transaction": None,
            }

        if intent == "rule_query":
            return {
                "intent": "rule_query",
                "rule_type": data.get("rule_type"),
                "reply": data.get("reply", ""),
                "business_type": None,
                "transaction": None,
            }

        if intent == "rule_mgmt":
            return {
                "intent": "rule_mgmt",
                "action": data.get("action", "create"),
                "rule_type": data.get("rule_type"),
                "reply": data.get("reply", ""),
                "business_type": None,
                "transaction": None,
            }

        if intent == "voucher_query":
            return {
                "intent": "voucher_query",
                "status": data.get("status"),
                "reply": data.get("reply", ""),
                "business_type": None,
                "transaction": None,
            }

        if intent == "user_mgmt":
            return {
                "intent": "user_mgmt",
                "action": data.get("action", "create"),
                "new_username": data.get("new_username"),
                "new_display_name": data.get("new_display_name"),
                "new_role": data.get("new_role", "user"),
                "new_password": data.get("new_password"),
                "reply": data.get("reply", ""),
                "business_type": None,
                "transaction": None,
            }

        # intent == "business"
        business_type = data.get("business_type", "other")

        if business_type != "sales_revenue":
            return {"intent": "business", "business_type": business_type, "transaction": None}

        if data.get("tax_excluded_amount") is None or data.get("total_amount") is None:
            return {"intent": "business", "business_type": business_type, "transaction": None}

        txn = SalesTransaction(
            transaction_id=data["transaction_id"],
            company_code=data.get("company_code", "1000"),
            document_date=data.get("document_date", today),
            posting_date=data.get("posting_date", today),
            customer_code=data.get("customer_code", "C99999"),
            customer_name=data.get("customer_name", "未知客户"),
            product_type=data.get("product_type", "service"),
            contract_no=data.get("contract_no", ""),
            invoice_no=data.get("invoice_no", ""),
            currency=data.get("currency", "CNY"),
            tax_rate=Decimal(str(data.get("tax_rate", "0.13"))),
            tax_excluded_amount=Decimal(str(data["tax_excluded_amount"])),
            tax_amount=Decimal(str(data.get("tax_amount", "0"))),
            total_amount=Decimal(str(data["total_amount"])),
            profit_center=data.get("profit_center", "PC-DEFAULT"),
            cost_center=data.get("cost_center", "CC-DEFAULT"),
        )
        return {"intent": "business", "business_type": business_type, "transaction": txn}
