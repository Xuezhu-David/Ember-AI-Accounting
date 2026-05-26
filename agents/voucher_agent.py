"""Voucher generation agent — generates accounting vouchers from business transactions."""

import json
import logging
from dataclasses import asdict
from decimal import Decimal

from agentscope.agent import Agent
from agentscope.event import (
    ModelCallStartEvent,
    ModelCallEndEvent,
    TextBlockStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
)
from agentscope.message import Msg, UserMsg, AssistantMsg

from llm_voucher_generator import _parse_llm_response, _extract_json
from prompts import VOUCHER_GENERATION_PROMPT
from voucher_models import SalesTransaction
from voucher_rules import build_sales_revenue_voucher

from .model_factory import create_chat_model

logger = logging.getLogger(__name__)


class VoucherAgent(Agent):
    """Generate accounting voucher drafts from SalesTransaction data."""

    def __init__(self, name: str) -> None:
        super().__init__(
            name=name,
            system_prompt=VOUCHER_GENERATION_PROMPT,
            model=create_chat_model(),
        )

    async def reply(self, msg: Msg) -> Msg:
        txn = self._extract_transaction(msg)
        user_prompt = _build_user_prompt(txn)
        await self.observe(UserMsg(name="user", content=user_prompt))
        return await super().reply(msg)

    async def _reasoning_impl(self, tool_choice=None):
        yield ModelCallStartEvent(
            reply_id=self.state.reply_id,
            model_name=self.model.model,
        )

        kwargs = await self._prepare_model_input()
        txn = self._extract_transaction_from_context()

        try:
            response = await self._call_model(messages=kwargs["messages"], tools=[])
            result_msg = AssistantMsg(name=self.name, content=list(response.content))
            raw = result_msg.get_text_content() or ""
            voucher = _parse_llm_response(raw, txn)
            voucher_dict = _voucher_to_dict(voucher)
            text = json.dumps(voucher_dict, ensure_ascii=False, default=str)
            metadata = {"status": "generated", "voucher": voucher}
        except Exception as exc:
            logger.warning("LLM voucher generation failed (%s: %s), falling back to rule engine.", type(exc).__name__, exc)
            voucher = build_sales_revenue_voucher(txn)
            voucher_dict = _voucher_to_dict(voucher)
            text = json.dumps(voucher_dict, ensure_ascii=False, default=str)
            metadata = {"status": "generated", "voucher": voucher}

        yield ModelCallEndEvent(reply_id=self.state.reply_id, input_tokens=0, output_tokens=0)

        block_id = __import__("uuid").uuid4().hex
        yield TextBlockStartEvent(reply_id=self.state.reply_id, block_id=block_id)
        yield TextBlockDeltaEvent(reply_id=self.state.reply_id, block_id=block_id, delta=text)
        yield TextBlockEndEvent(reply_id=self.state.reply_id, block_id=block_id)

        yield AssistantMsg(
            id=self.state.reply_id,
            name=self.name,
            content=text,
            metadata=metadata,
        )

    def _extract_transaction(self, msg: Msg) -> SalesTransaction:
        """Extract SalesTransaction from message content or metadata."""
        if msg.metadata and "transaction" in msg.metadata:
            return msg.metadata["transaction"]

        data = json.loads(msg.get_text_content() or "{}")
        return _dict_to_transaction(data)

    def _extract_transaction_from_context(self) -> SalesTransaction:
        """Extract SalesTransaction from the last user message in context."""
        for msg in reversed(self.state.context):
            if msg.role == "user" and msg.metadata and "transaction" in msg.metadata:
                return msg.metadata["transaction"]
        raise ValueError("No transaction found in agent context")


def _build_user_prompt(txn: SalesTransaction) -> str:
    txn_dict = asdict(txn)
    for key, value in txn_dict.items():
        if isinstance(value, Decimal):
            txn_dict[key] = str(value)

    return (
        "请根据以下销售业务数据生成会计凭证草稿：\n\n"
        f"```json\n{json.dumps(txn_dict, ensure_ascii=False, indent=2)}\n```"
    )


def _dict_to_transaction(data: dict) -> SalesTransaction:
    return SalesTransaction(
        transaction_id=data["transaction_id"],
        company_code=data.get("company_code", "1000"),
        document_date=data.get("document_date", ""),
        posting_date=data.get("posting_date", ""),
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


def _voucher_to_dict(voucher) -> dict:
    """Convert a Voucher object to a frontend-compatible dict."""
    rows = []
    for line in voucher.lines:
        debit = float(line.amount) if line.debit_credit == "S" else 0
        credit = float(line.amount) if line.debit_credit == "H" else 0
        rows.append({
            "line_no": line.line_no,
            "account_code": line.account_code,
            "account_name": line.account_name,
            "debit_credit": line.debit_credit,
            "debit": debit,
            "credit": credit,
            "currency": line.currency,
            "customer_code": line.customer_code,
            "customer_name": line.customer_name,
            "tax_code": line.tax_code,
            "profit_center": line.profit_center,
            "cost_center": line.cost_center,
            "assignment": line.assignment,
            "text": line.text,
        })
    return {
        "voucher_id": voucher.voucher_id,
        "company_code": voucher.company_code,
        "document_type": voucher.document_type,
        "document_date": voucher.document_date,
        "posting_date": voucher.posting_date,
        "reference": voucher.reference,
        "header_text": voucher.header_text,
        "confidence": str(voucher.confidence),
        "warnings": voucher.warnings,
        "rows": rows,
    }
