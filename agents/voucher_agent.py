"""Voucher generation agent — generates accounting vouchers from business transactions."""

import json
import logging

from agentscope.message import Msg

from llm_voucher_generator import LLMVoucherGenerator
from voucher_models import SalesTransaction

logger = logging.getLogger(__name__)


class VoucherAgent:
    """Generate accounting voucher drafts from SalesTransaction data."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._generator = LLMVoucherGenerator()

    async def reply(self, msg: Msg) -> Msg:
        try:
            txn = self._extract_transaction(msg)
            voucher = await self._generator.generate(txn)
            voucher_dict = _voucher_to_dict(voucher)
            return Msg(
                name=self.name,
                role="assistant",
                content=json.dumps(voucher_dict, ensure_ascii=False, default=str),
                metadata={"status": "generated", "voucher": voucher},
            )
        except Exception as exc:
            logger.error("VoucherAgent generation failed: %s", exc)
            return Msg(
                name=self.name,
                role="assistant",
                content="{}",
                metadata={"status": "error", "error": str(exc)},
            )

    def _extract_transaction(self, msg: Msg) -> SalesTransaction:
        """Extract SalesTransaction from message content or metadata."""
        if msg.metadata and "transaction" in msg.metadata:
            return msg.metadata["transaction"]

        data = json.loads(msg.get_text_content() or "{}")
        from decimal import Decimal
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
