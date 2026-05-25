"""Agent that handles user instructions plus Excel attachments using LLM."""

import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from agentscope.agent import AgentBase
from agentscope.message import Msg

from excel_loader import load_sales_transactions
from llm_voucher_generator import LLMVoucherGenerator
from sap_exporter import export_sap_csv
from voucher_models import Voucher


class AttachmentVoucherAgent(AgentBase):
    """Parse attachment instructions and produce accounting voucher drafts using LLM."""

    def __init__(self, name: str, output_dir: str | Path = "data/output") -> None:
        super().__init__()
        self.name = name
        self.output_dir = Path(output_dir)
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
            result = {"status": "error", "message": "No instruction was provided."}
        else:
            result = await self._handle_request(msg)

        response = Msg(
            name=self.name,
            role="assistant",
            content=json.dumps(result, ensure_ascii=False, indent=2, default=_json_default),
            metadata={
                "status": result["status"],
                "voucher_count": len(self.generated_vouchers),
            },
        )
        await self.print(response)
        return response

    async def _handle_request(self, msg: Msg) -> dict:
        request = json.loads(msg.get_text_content() or "{}")
        instruction = request.get("instruction", "")
        attachment_paths = [Path(path) for path in request.get("attachments", [])]

        if not _is_sales_revenue_instruction(instruction):
            return {
                "status": "unsupported_instruction",
                "message": "Current MVP only supports sales revenue voucher generation.",
                "instruction": instruction,
            }

        if not attachment_paths:
            return {
                "status": "missing_attachment",
                "message": "Please provide at least one Excel attachment path.",
            }

        vouchers: list[Voucher] = []
        parsed_files = []
        for attachment_path in attachment_paths:
            transactions = load_sales_transactions(attachment_path)
            parsed_files.append(
                {
                    "file": str(attachment_path),
                    "transaction_count": len(transactions),
                },
            )
            for txn in transactions:
                voucher = await self._generator.generate(txn)
                vouchers.append(voucher)

        self.generated_vouchers.extend(vouchers)
        output_path = self.output_dir / "sap_sales_revenue_from_attachments.csv"
        export_sap_csv(vouchers, output_path)

        return {
            "status": "voucher_generated",
            "instruction": instruction,
            "parsed_files": parsed_files,
            "voucher_count": len(vouchers),
            "output_path": str(output_path),
            "vouchers": [_voucher_to_jsonable(voucher) for voucher in vouchers],
        }


def _is_sales_revenue_instruction(instruction: str) -> bool:
    normalized = instruction.lower()
    return any(keyword in normalized for keyword in ["销售收入", "sales revenue", "收入凭证"])


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


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
