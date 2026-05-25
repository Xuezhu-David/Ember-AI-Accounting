"""Sales revenue voucher generation demo (LLM-powered).

Uses an LLM to turn sales transactions into accounting voucher drafts,
then exports them in SAP import format.

Run:
    source .venv/bin/activate
    python sales_revenue_demo.py
"""

import asyncio
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import agentscope
from agentscope.message import Msg

from accounting_agent import AccountingVoucherAgent
from llm_voucher_generator import MODEL_NAME
from sap_exporter import export_sap_csv
from voucher_models import SalesTransaction

OUTPUT_PATH = Path("data/output/sap_sales_revenue_vouchers.csv")


def sample_sales_transaction() -> SalesTransaction:
    """Create one sample invoiced sales transaction."""

    return SalesTransaction(
        transaction_id="SO-20260520-001",
        company_code="1000",
        document_date="2026-05-20",
        posting_date="2026-05-20",
        customer_code="C10086",
        customer_name="Shanghai Demo Customer Co., Ltd.",
        product_type="software",
        contract_no="CTR-2026-SW-001",
        invoice_no="INV-20260520-0001",
        currency="CNY",
        tax_rate=Decimal("0.13"),
        tax_excluded_amount=Decimal("100000.00"),
        tax_amount=Decimal("13000.00"),
        total_amount=Decimal("113000.00"),
        profit_center="PC-SOFTWARE",
        cost_center="CC-SALES",
    )


def json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


async def main() -> None:
    agentscope.init(
        project="AIAccountingVoucherDemo",
        name="llm_sales_revenue_to_sap_voucher",
    )

    agent = AccountingVoucherAgent("sales_revenue_voucher_agent")
    transaction = sample_sales_transaction()
    request = Msg(
        name="sales_system",
        role="user",
        content=json.dumps(
            asdict(transaction),
            ensure_ascii=False,
            default=json_default,
        ),
    )

    print("Input sales transaction:")
    print(json.dumps(asdict(transaction), ensure_ascii=False, indent=2, default=json_default))
    print()

    response = await agent.reply(request)
    export_sap_csv(agent.generated_vouchers, OUTPUT_PATH)

    print()
    print("Voucher JSON draft (LLM generated):")
    print(response.get_text_content())
    print()
    print(f"SAP-style CSV exported to: {OUTPUT_PATH}")
    print(f"LLM model used: {MODEL_NAME}")


if __name__ == "__main__":
    asyncio.run(main())
