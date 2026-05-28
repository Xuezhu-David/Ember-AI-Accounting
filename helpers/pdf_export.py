"""PDF export for voucher records using WeasyPrint + Jinja2."""

import json
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


async def generate_voucher_pdf(record: dict) -> bytes | None:
    """Generate a PDF for a voucher record. Returns PDF bytes or None on failure."""
    try:
        voucher_data = json.loads(record.get("voucher_data") or "{}")
        rows = voucher_data.get("rows", [])
        total_debit = sum(r.get("debit", 0) for r in rows)
        total_credit = sum(r.get("credit", 0) for r in rows)

        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        template = env.get_template("voucher_pdf.html")
        html_content = template.render(
            voucher_id=record.get("voucher_id", ""),
            company_code=record.get("company_code", ""),
            document_type=record.get("document_type", ""),
            document_date=record.get("document_date", ""),
            posting_date=record.get("posting_date", ""),
            reference=record.get("reference", ""),
            header_text=record.get("header_text", ""),
            confidence=record.get("confidence", ""),
            status=record.get("status", "draft"),
            created_at=record.get("created_at", ""),
            posted_at=record.get("posted_at", ""),
            posted_by_name=record.get("posted_by_name", ""),
            user_display_name=record.get("user_display_name", ""),
            rows=rows,
            total_debit=total_debit,
            total_credit=total_credit,
        )

        from weasyprint import HTML
        pdf_bytes = HTML(string=html_content).write_pdf()
        return pdf_bytes
    except Exception:
        logger.exception("Failed to generate PDF for voucher %s", record.get("voucher_id"))
        return None
