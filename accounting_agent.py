"""AgentScope agent that uses LLM to generate accounting voucher drafts."""

import json
from dataclasses import asdict
from decimal import Decimal

from agentscope.message import Msg

from llm_voucher_generator import LLMVoucherGenerator
from voucher_models import SalesTransaction, Voucher


class AccountingVoucherAgent:
    """Generate accounting voucher drafts from normalized business records using LLM."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.history: list[Msg] = []
        self.generated_vouchers: list[Voucher] = []
        self._generator = LLMVoucherGenerator()

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        if msg is None:
            return
        if isinstance(msg, list):
            self.history.extend(msg)
        else:
            self.history.append(msg)

    async def reply(self, msg: Msg | None = None) -> Msg:
        await self.observe(msg)

        if msg is None:
            voucher = None
        else:
            txn = self._transaction_from_message(msg)
            voucher = await self._generator.generate(txn)
            self.generated_vouchers.append(voucher)

        status = "no_transaction" if voucher is None else "voucher_generated"
        content = (
            "No transaction was provided."
            if voucher is None
            else json.dumps(
                _voucher_to_jsonable(voucher),
                ensure_ascii=False,
                indent=2,
            )
        )

        return Msg(
            name=self.name,
            role="assistant",
            content=content,
            metadata={
                "status": status,
                "voucher_count": len(self.generated_vouchers),
            },
        )

    def _transaction_from_message(self, msg: Msg) -> SalesTransaction:
        payload = json.loads(msg.get_text_content() or "{}")
        decimal_fields = {
            "tax_rate",
            "tax_excluded_amount",
            "tax_amount",
            "total_amount",
        }
        for field in decimal_fields:
            payload[field] = Decimal(str(payload[field]))
        return SalesTransaction(**payload)


def _voucher_to_jsonable(voucher: Voucher) -> dict:
    def convert(value: object) -> object:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, list):
            return [convert(item) for item in value]
        if hasattr(value, "__dataclass_fields__"):
            return convert(asdict(value))
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        return value

    return convert(voucher)
