"""OCR agent — extracts transaction data from invoice images and PDFs."""

import base64
import json
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

from agentscope.message import Msg, UserMsg, SystemMsg, DataBlock, TextBlock, Base64Source

from prompts import IMAGE_PARSE_SYSTEM_PROMPT
from voucher_models import SalesTransaction

from .model_factory import create_chat_model

logger = logging.getLogger(__name__)


class OcrAgent:
    """Extract structured transaction data from invoice images or PDFs."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.model = create_chat_model(vision=True)

    async def reply(self, msg: Msg) -> Msg:
        file_path = Path(msg.metadata.get("file_path", "")) if msg.metadata else Path()
        file_type = msg.metadata.get("file_type", "image") if msg.metadata else "image"

        try:
            if file_type == "pdf":
                result_data = await self._parse_pdf(file_path)
            else:
                result_data = await self._parse_image(file_path)
        except Exception as exc:
            logger.error("OcrAgent parse failed: %s", exc)
            result_data = None

        return Msg(
            name=self.name,
            role="assistant",
            content=json.dumps(result_data, ensure_ascii=False, default=str) if result_data else "{}",
            metadata={"ocr_result": result_data},
        )

    async def _parse_image(self, image_path: Path) -> dict | None:
        """Parse a single image file."""
        image_bytes = image_path.read_bytes()
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        ext = image_path.suffix.lower()
        mime_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        }
        mime_type = mime_map.get(ext, "image/jpeg")
        today = date.today().strftime("%Y-%m-%d")

        messages: list[Msg] = [
            SystemMsg(name="system", content=IMAGE_PARSE_SYSTEM_PROMPT),
            UserMsg(name="user", content=[
                DataBlock(source=Base64Source(data=b64_image, media_type=mime_type)),
                TextBlock(text=f"当前日期：{today}\n\n请识别这张发票/单据图片，提取交易数据。"),
            ]),
        ]

        response = await self.model(messages)
        raw = response.get_text_content() or ""
        return self._parse_llm_response(raw, today)

    async def _parse_pdf(self, pdf_path: Path) -> dict | None:
        """Parse a PDF file (convert pages to images first)."""
        pages = _pdf_to_images(pdf_path)
        if not pages:
            return None

        today = date.today().strftime("%Y-%m-%d")
        blocks = []
        for img_bytes, mime_type in pages:
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            blocks.append(DataBlock(source=Base64Source(data=b64, media_type=mime_type)))

        page_note = ""
        if len(pages) > 1:
            page_note = f"该PDF共{len(pages)}页，请识别其中包含发票/单据的页面并提取数据。"

        blocks.append(TextBlock(text=f"当前日期：{today}\n\n请识别这张发票/单据，提取交易数据。{page_note}"))

        messages: list[Msg] = [
            SystemMsg(name="system", content=IMAGE_PARSE_SYSTEM_PROMPT),
            UserMsg(name="user", content=blocks),
        ]

        response = await self.model(messages)
        raw = response.get_text_content() or ""
        return self._parse_llm_response(raw, today)

    def _parse_llm_response(self, raw: str, today: str) -> dict | None:
        """Parse LLM JSON response into a structured result."""
        json_str = raw.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        data = json.loads(json_str)
        business_type = data.get("business_type", "other")

        if business_type != "sales_revenue":
            return {"business_type": business_type, "transaction": None}

        if data.get("tax_excluded_amount") is None or data.get("total_amount") is None:
            return {"business_type": business_type, "transaction": None}

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
        return {"business_type": business_type, "transaction": txn}


def _pdf_to_images(pdf_path: Path) -> list[tuple[bytes, str]]:
    """Convert PDF pages to PNG images."""
    import fitz

    doc = fitz.open(str(pdf_path))
    pages = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        pages.append((pix.tobytes("png"), "image/png"))
    doc.close()
    return pages
