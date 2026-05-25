"""FastAPI backend for the AI Accounting Voucher web app.

Provides REST APIs for:
  - Chat: natural language → LLM → voucher draft
  - File upload: Excel attachment → parse → LLM → voucher draft
  - Confirm: mark voucher as posted

Run:
    source .venv/bin/activate
    python server.py
"""

import asyncio
import json
import logging
import shutil
import uuid
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from excel_loader import load_sales_transactions
from llm_voucher_generator import LLMVoucherGenerator
from sap_exporter import export_sap_csv
from voucher_models import Voucher, VoucherLine
from voucher_rules import build_sales_revenue_voucher, load_voucher_rule_lines

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── App setup ────────────────────────────────────────────────────────────────

app = FastAPI(title="Ember AI Accounting", version="1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Paths ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent
UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"
SESSION_DIR = PROJECT_ROOT / "data" / "sessions"
POSTED_CSV = PROJECT_ROOT / "data" / "output" / "posted_vouchers.csv"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)
POSTED_CSV.parent.mkdir(parents=True, exist_ok=True)

# ── Session persistence ─────────────────────────────────────────────────────

generator = LLMVoucherGenerator()


def _session_path(session_id: str) -> Path:
    return SESSION_DIR / f"{session_id}.json"


def _load_session(session_id: str) -> dict[str, Any]:
    """Load session from disk, or create a new one."""
    path = _session_path(session_id)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            # Re-hydrate Voucher objects from dicts
            raw["vouchers"] = [_dict_to_voucher(v) for v in raw.get("vouchers", [])]
            return raw
        except Exception:
            pass
    return {"vouchers": [], "uploaded_files": []}


def _save_session(session_id: str, session: dict[str, Any]) -> None:
    """Persist session to disk (vouchers stored as JSON-serialisable dicts)."""
    path = _session_path(session_id)
    data = {
        "vouchers": [_voucher_to_json(v) for v in session.get("vouchers", [])],
        "uploaded_files": session.get("uploaded_files", []),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_session(session_id: str | None) -> tuple[str, dict[str, Any]]:
    """Get or create a session. Returns (session_id, session_dict)."""
    sid = session_id or str(uuid.uuid4())
    session = _load_session(sid)
    return sid, session


# ── Helper: voucher ↔ JSON serialisable dict ─────────────────────────────────

def _voucher_to_json(voucher: Voucher) -> dict:
    """Convert a Voucher model to a JSON-safe dict for persistence."""
    from dataclasses import asdict

    def _convert(value: object) -> object:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, list):
            return [_convert(item) for item in value]
        if hasattr(value, "__dataclass_fields__"):
            return _convert(asdict(value))
        if isinstance(value, dict):
            return {k: _convert(v) for k, v in value.items()}
        return value

    return _convert(voucher)


def _dict_to_voucher(data: dict) -> Voucher:
    """Reconstruct a Voucher (with VoucherLine list) from a JSON dict."""
    lines = [
        VoucherLine(
            line_no=ln["line_no"],
            debit_credit=ln["debit_credit"],
            account_code=ln["account_code"],
            account_name=ln["account_name"],
            amount=Decimal(str(ln["amount"])),
            currency=ln["currency"],
            customer_code=ln.get("customer_code", ""),
            customer_name=ln.get("customer_name", ""),
            tax_code=ln.get("tax_code", ""),
            profit_center=ln.get("profit_center", ""),
            cost_center=ln.get("cost_center", ""),
            assignment=ln.get("assignment", ""),
            text=ln.get("text", ""),
        )
        for ln in data.get("lines", [])
    ]
    return Voucher(
        voucher_id=data["voucher_id"],
        company_code=data["company_code"],
        document_type=data["document_type"],
        document_date=data["document_date"],
        posting_date=data["posting_date"],
        reference=data["reference"],
        header_text=data["header_text"],
        source_transaction_id=data["source_transaction_id"],
        confidence=Decimal(str(data["confidence"])),
        warnings=data.get("warnings", []),
        lines=lines,
    )


# ── Helper: voucher → frontend-friendly dict ─────────────────────────────────

def _voucher_to_front(voucher: Voucher) -> dict:
    """Convert a Voucher model to the JSON shape the frontend expects."""
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


# ── API: Chat ────────────────────────────────────────────────────────────────

@app.post("/api/chat")
async def chat(payload: dict):
    """Accept a natural language message and optional session_id, return AI response + voucher data."""
    message = payload.get("message", "").strip()
    session_id = payload.get("session_id")
    session_id, session = _get_session(session_id)

    if not message:
        return JSONResponse({
            "reply": "请描述一笔业务，比如「请客户吃饭花了1200元」，或上传一张发票。",
            "session_id": session_id,
        })

    # Build a synthetic SalesTransaction from the natural language via LLM
    parse_result = await _parse_transaction_from_nl(message)
    if parse_result is None:
        return JSONResponse({
            "reply": "抱歉，我暂时无法理解。请尝试更具体的描述，例如「销售软件产品给XX公司，不含税金额100000元，税率13%」，或上传Excel附件。",
            "session_id": session_id,
        })

    # Handle chat intent — direct reply, no voucher generation
    if parse_result.get("intent") == "chat":
        return JSONResponse({
            "reply": parse_result["reply"],
            "session_id": session_id,
        })

    # Handle rule_query intent — show voucher rules
    if parse_result.get("intent") == "rule_query":
        rule_type = parse_result.get("rule_type")
        reply = parse_result.get("reply", "")

        if rule_type is None:
            # User didn't specify a type — guide them to pick one
            available_types = {
                "sales_revenue": "销售收入",
            }
            type_list = "\n".join(f"  {i+1}. {desc}" for i, desc in enumerate(available_types.values()))
            if not reply:
                reply = f"目前系统支持以下凭证类型的规则查看：\n{type_list}\n\n请告诉我您想查看哪种类型的凭证规则？"
            return JSONResponse({
                "reply": reply,
                "session_id": session_id,
            })

        # User specified a type — load and return the matching rules
        try:
            rule_lines = load_voucher_rule_lines()
        except Exception as exc:
            logger.error("Failed to load voucher rules: %s", exc)
            return JSONResponse({
                "reply": "加载凭证规则时出错，请稍后重试。",
                "session_id": session_id,
            })

        # Filter rules by the requested business_type
        filtered_rules: dict[str, list[dict]] = {}
        for rl in rule_lines:
            if rl.business_type != rule_type:
                continue
            if rl.rule_code not in filtered_rules:
                filtered_rules[rl.rule_code] = {
                    "rule_code": rl.rule_code,
                    "business_type": rl.business_type,
                    "product_type": rl.product_type,
                    "tax_rate": rl.tax_rate,
                    "document_type": rl.document_type,
                    "lines": [],
                }
            filtered_rules[rl.rule_code]["lines"].append({
                "line_no": rl.line_no,
                "debit_credit": rl.debit_credit,
                "debit_credit_display": "借" if rl.debit_credit == "S" else "贷",
                "account_code": rl.account_code,
                "account_name": rl.account_name,
                "amount_field": rl.amount_field,
                "amount_field_display": {
                    "total_amount": "价税合计",
                    "tax_excluded_amount": "不含税金额",
                    "tax_amount": "税额",
                }.get(rl.amount_field, rl.amount_field),
                "customer_source": rl.customer_source,
                "tax_code_rule": rl.tax_code_rule,
                "profit_center_source": rl.profit_center_source,
                "cost_center_source": rl.cost_center_source,
                "assignment_source": rl.assignment_source,
                "text_template": rl.text_template,
            })

        rules_list = list(filtered_rules.values())
        biz_type_labels = {
            "sales_revenue": "销售收入",
            "expense": "费用报销",
            "asset_purchase": "资产采购",
            "salary": "工资薪酬",
            "loan": "借款/还款",
        }
        biz_label = biz_type_labels.get(rule_type, rule_type)

        if not rules_list:
            if not reply:
                reply = f"暂无「{biz_label}」类型的凭证规则配置。"
            return JSONResponse({
                "reply": reply,
                "session_id": session_id,
            })

        if not reply:
            reply = f"以下是「{biz_label}」类型的凭证规则，共 {len(rules_list)} 条："
        return JSONResponse({
            "reply": reply,
            "session_id": session_id,
            "rules": rules_list,
            "rule_type": rule_type,
        })

    business_type = parse_result["business_type"]
    txn = parse_result["transaction"]

    # Check if the business type is supported
    if business_type not in SUPPORTED_BUSINESS_TYPES or txn is None:
        supported_list = "\n".join(
            f"  - {desc}" for desc in SUPPORTED_BUSINESS_TYPES.values()
        )
        type_display = {
            "expense": "费用报销",
            "asset_purchase": "资产采购",
            "salary": "工资薪酬",
            "loan": "借款/还款",
            "other": "其他",
        }.get(business_type, business_type)

        return JSONResponse({
            "reply": (
                f"抱歉，当前系统暂不支持「{type_display}」类型的凭证生成。\n\n"
                f"目前支持的凭证类型：\n{supported_list}\n\n"
                "请描述一笔支持的业务，或上传Excel附件。"
            ),
            "session_id": session_id,
        })

    voucher = await generator.generate(txn)
    session["vouchers"].append(voucher)
    _save_session(session_id, session)

    reply = f"已为您生成凭证草稿（置信度 {voucher.confidence}）。"
    if voucher.warnings:
        reply += f" ⚠️ 注意：{'；'.join(voucher.warnings)}"

    return JSONResponse({
        "reply": reply,
        "session_id": session_id,
        "voucher": _voucher_to_front(voucher),
    })


# ── API: File Upload ─────────────────────────────────────────────────────────

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
PDF_EXTENSIONS = {".pdf"}

IMAGE_PARSE_SYSTEM_PROMPT = """\
你是一个财务单据识别助手。用户会上传一张发票或财务单据的图片，你需要从中识别并提取结构化的交易数据。

## 业务类型判断规则

- sales_revenue：销售发票（如增值税专用发票、普通发票，属于销售方开出的）
- expense：费用报销单据（如餐饮发票、差旅发票、办公用品发票，属于购买方收到的）
- asset_purchase：采购固定资产的发票
- salary：工资薪酬相关
- loan：借款或还款
- other：其他无法归类

## 目前系统仅支持处理的业务类型
- sales_revenue（销售收入）

如果 business_type 不是 sales_revenue，只需输出 business_type 字段即可，其他字段可以省略。

请严格按照以下JSON格式输出，不要包含任何其他文字：

```json
{
  "business_type": "sales_revenue / expense / asset_purchase / salary / loan / other",
  "transaction_id": "自动生成，格式 SO-YYYYMMDD-XXX",
  "company_code": "从发票中提取，若无则用 1000",
  "document_date": "开票日期，YYYY-MM-DD",
  "posting_date": "与document_date相同",
  "customer_code": "购买方纳税人识别号或编码，若无则用 C99999",
  "customer_name": "购买方名称",
  "product_type": "software / service / saas / goods 之一，根据货物或应税劳务名称判断",
  "contract_no": "若无则生成 CTR-YYYY-XX-XXX",
  "invoice_no": "发票号码",
  "currency": "CNY",
  "tax_rate": "从税率栏提取，如 0.13 / 0.06 / 0.00",
  "tax_excluded_amount": "不含税金额，精确到分",
  "tax_amount": "税额，精确到分",
  "total_amount": "价税合计，精确到分",
  "profit_center": "若无则用 PC-DEFAULT",
  "cost_center": "若无则用 CC-DEFAULT"
}
```

如果图片模糊无法识别，或不是财务单据，请输出：
```json
{"business_type": "other"}
```
"""


@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    session_id: str | None = None,
):
    """Upload an Excel or image file, parse it, generate vouchers via LLM."""
    session_id, session = _get_session(session_id)

    # Save uploaded file
    file_id = str(uuid.uuid4())[:8]
    suffix = Path(file.filename or "upload.xlsx").suffix.lower()
    saved_path = UPLOAD_DIR / f"{file_id}{suffix}"
    with saved_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    file_info = {
        "name": file.filename,
        "size": file.size or saved_path.stat().st_size,
        "path": str(saved_path),
    }
    session["uploaded_files"].append(file_info)

    # ── Image / PDF path: use multimodal LLM for OCR ──
    if suffix in IMAGE_EXTENSIONS or suffix in PDF_EXTENSIONS:
        if suffix in PDF_EXTENSIONS:
            result = await _parse_pdf_to_transaction(saved_path)
        else:
            result = await _parse_image_to_transaction(saved_path)

        source_label = "PDF" if suffix in PDF_EXTENSIONS else "图片"

        if result is None:
            return JSONResponse({
                "reply": f"{source_label}识别失败，无法提取有效信息。请确保内容清晰且包含完整的发票/单据信息。",
                "session_id": session_id,
                "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)},
            })

        business_type = result.get("business_type", "other")
        if business_type not in SUPPORTED_BUSINESS_TYPES:
            supported_list = "\n".join(
                f"  - {desc}" for desc in SUPPORTED_BUSINESS_TYPES.values()
            )
            type_display = {
                "expense": "费用报销",
                "asset_purchase": "资产采购",
                "salary": "工资薪酬",
                "loan": "借款/还款",
                "other": "其他",
            }.get(business_type, business_type)
            return JSONResponse({
                "reply": (
                    f"已识别单据类型为「{type_display}」，但当前系统暂不支持该类型的凭证生成。\n\n"
                    f"目前支持的凭证类型：\n{supported_list}"
                ),
                "session_id": session_id,
                "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)},
            })

        txn = result.get("transaction")
        if txn is None:
            return JSONResponse({
                "reply": f"{source_label}识别成功，但未能提取到完整的交易金额信息。请上传更清晰的文件。",
                "session_id": session_id,
                "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)},
            })

        voucher = await generator.generate(txn)
        session["vouchers"].append(voucher)
        _save_session(session_id, session)

        # Export to SAP CSV
        output_path = PROJECT_ROOT / "data" / "output" / f"sap_{file_id}.csv"
        export_sap_csv([voucher], output_path)

        return JSONResponse({
            "reply": f"已从{source_label}中识别出1笔销售收入交易，生成了1张凭证草稿。",
            "session_id": session_id,
            "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)},
            "vouchers": [_voucher_to_front(voucher)],
        })

    # ── Excel path: original logic ──
    try:
        transactions = load_sales_transactions(saved_path)
    except Exception as exc:
        logger.warning("Failed to parse uploaded file: %s", exc)
        return JSONResponse({
            "reply": f"文件解析失败：{exc}。请确保Excel格式正确。",
            "session_id": session_id,
        })

    vouchers = []
    for txn in transactions:
        voucher = await generator.generate(txn)
        session["vouchers"].append(voucher)
        vouchers.append(voucher)

    _save_session(session_id, session)

    reply = f"已解析 {len(transactions)} 笔交易，生成了 {len(vouchers)} 张凭证草稿。"

    # Export to SAP CSV
    output_path = PROJECT_ROOT / "data" / "output" / f"sap_{file_id}.csv"
    export_sap_csv(vouchers, output_path)

    return JSONResponse({
        "reply": reply,
        "session_id": session_id,
        "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)},
        "vouchers": [_voucher_to_front(v) for v in vouchers],
    })


# ── API: Voucher Rules ───────────────────────────────────────────────────────

@app.get("/api/rules")
async def get_voucher_rules():
    """Return the current voucher rule configuration as JSON."""
    try:
        rule_lines = load_voucher_rule_lines()
    except Exception as exc:
        logger.error("Failed to load voucher rules: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    # Group by rule_code for better display
    rules_grouped: dict[str, list[dict]] = {}
    for rl in rule_lines:
        if rl.rule_code not in rules_grouped:
            rules_grouped[rl.rule_code] = {
                "rule_code": rl.rule_code,
                "business_type": rl.business_type,
                "product_type": rl.product_type,
                "tax_rate": rl.tax_rate,
                "document_type": rl.document_type,
                "lines": [],
            }
        rules_grouped[rl.rule_code]["lines"].append({
            "line_no": rl.line_no,
            "debit_credit": rl.debit_credit,
            "debit_credit_display": "借" if rl.debit_credit == "S" else "贷",
            "account_code": rl.account_code,
            "account_name": rl.account_name,
            "amount_field": rl.amount_field,
            "amount_field_display": {
                "total_amount": "价税合计",
                "tax_excluded_amount": "不含税金额",
                "tax_amount": "税额",
            }.get(rl.amount_field, rl.amount_field),
            "customer_source": rl.customer_source,
            "tax_code_rule": rl.tax_code_rule,
            "profit_center_source": rl.profit_center_source,
            "cost_center_source": rl.cost_center_source,
            "assignment_source": rl.assignment_source,
            "text_template": rl.text_template,
        })

    return JSONResponse({
        "rules": list(rules_grouped.values()),
        "total_rules": len(rules_grouped),
        "total_lines": len(rule_lines),
    })

@app.post("/api/confirm")
async def confirm_voucher(payload: dict):
    """Mark a voucher as posted: append to posted_vouchers.csv + update session."""
    session_id = payload.get("session_id")
    voucher_id = payload.get("voucher_id")
    session_id, session = _get_session(session_id)

    voucher = None
    for v in session.get("vouchers", []):
        if v.voucher_id == voucher_id:
            voucher = v
            break

    if not voucher:
        return JSONResponse({"status": "not_found", "message": f"凭证 {voucher_id} 不存在"})

    # Append to posted_vouchers.csv
    _append_posted_csv(voucher)

    # Update session: mark voucher as posted
    session["posted_voucher_ids"] = session.get("posted_voucher_ids", [])
    if voucher_id not in session["posted_voucher_ids"]:
        session["posted_voucher_ids"].append(voucher_id)
    _save_session(session_id, session)

    logger.info("Voucher %s posted and saved to %s", voucher_id, POSTED_CSV)

    return JSONResponse({
        "status": "posted",
        "message": f"凭证 {voucher_id} 已成功过账，保存至 {POSTED_CSV.name}",
    })


def _append_posted_csv(voucher: Voucher) -> None:
    """Append one voucher's lines to the persistent posted_vouchers.csv."""
    import csv
    from sap_exporter import SAP_COLUMNS

    write_header = not POSTED_CSV.exists() or POSTED_CSV.stat().st_size == 0

    with POSTED_CSV.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SAP_COLUMNS)
        if write_header:
            writer.writeheader()
        for line in voucher.lines:
            writer.writerow({
                "BUKRS": voucher.company_code,
                "BLART": voucher.document_type,
                "BLDAT": voucher.document_date,
                "BUDAT": voucher.posting_date,
                "XBLNR": voucher.reference,
                "BKTXT": voucher.header_text,
                "BUZEI": line.line_no,
                "SHKZG": line.debit_credit,
                "HKONT": line.account_code,
                "ACCOUNT_NAME": line.account_name,
                "WRBTR": line.amount,
                "WAERS": line.currency,
                "KUNNR": line.customer_code,
                "CUSTOMER_NAME": line.customer_name,
                "MWSKZ": line.tax_code,
                "PRCTR": line.profit_center,
                "KOSTL": line.cost_center,
                "ZUONR": line.assignment,
                "SGTXT": line.text,
            })


# ── NL → Transaction via LLM ─────────────────────────────────────────────────

NL_PARSE_SYSTEM_PROMPT = """\
你是一个财务业务分类与数据抽取助手。用户会用自然语言描述一笔业务，你需要：
1. 先判断用户的意图（intent）
2. 如果是业务描述，再判断业务类型（business_type）并提取结构化数据

## 意图判断规则

- business：用户在描述一笔具体的财务/业务交易（如「卖软件给XX公司」「请客户吃饭花了560元」「采购了一台服务器」）
- rule_query：用户在询问凭证规则/记账规则（如「凭证规则是什么」「我想看销售收入凭证怎么记」「费用报销怎么入账」「凭证规则」）
- chat：用户在提问、闲聊、求助或与系统对话（如「你好」「你能做什么？」「什么是增值税？」）
- unknown：无法判断

如果 intent 不是 business，只需输出 intent 和相关字段，reply 中给出友好的回复。

## rule_query 意图的处理

当用户询问凭证规则时，需要判断用户问的是哪种业务类型的规则：
- 如果用户明确提到了业务类型（如「销售收入的规则」「费用报销怎么记」），则提取 rule_type
- 如果用户只是笼统地问（如「凭证规则是什么」「我想看规则」），则 rule_type 为 null

rule_type 的可选值：sales_revenue / expense / asset_purchase / salary / loan

## 业务类型判断规则（仅 intent=business 时需要）

- sales_revenue：销售商品或提供服务产生的收入（如「卖软件给XX公司」「提供咨询服务收费」）
- expense：日常费用支出（如「请客户吃饭」「打车」「买办公用品」「报销差旅费」）
- asset_purchase：购买固定资产或无形资产（如「采购服务器」「买办公设备」）
- salary：工资薪酬相关（如「发工资」「社保公积金」）
- loan：借款或还款（如「向银行贷款」「偿还借款」）
- other：其他无法归类的业务

## 目前系统仅支持处理的业务类型
- sales_revenue（销售收入）

如果 business_type 不是 sales_revenue，只需输出 business_type 字段即可，其他字段可以省略。

请严格按照以下JSON格式输出，不要包含任何其他文字：

### intent=chat 时：
```json
{
  "intent": "chat",
  "reply": "对用户问题的友好回答"
}
```

### intent=rule_query 时：
```json
{
  "intent": "rule_query",
  "rule_type": "sales_revenue / expense / asset_purchase / salary / loan / null",
  "reply": "对用户询问规则的引导性回复"
}
```
如果用户明确指定了业务类型，rule_type 填对应的值，reply 中确认并说明即将展示该类型的规则。
如果用户没有指定具体类型，rule_type 填 null，reply 中列出可查看规则的凭证类型，引导用户选择。

### intent=business 时：
```json
{
  "intent": "business",
  "business_type": "sales_revenue / expense / asset_purchase / salary / loan / other",
  "transaction_id": "自动生成，格式 SO-YYYYMMDD-XXX",
  "company_code": "1000",
  "document_date": "YYYY-MM-DD",
  "posting_date": "YYYY-MM-DD",
  "customer_code": "从描述中提取客户编码，若无则用 C99999",
  "customer_name": "从描述中提取客户名称",
  "product_type": "software / service / saas / goods 之一",
  "contract_no": "从描述中提取，若无则生成 CTR-YYYY-XX-XXX",
  "invoice_no": "从描述中提取，若无则生成 INV-YYYYMMDD-XXXX",
  "currency": "CNY",
  "tax_rate": "0.13 或 0.06 或 0.00",
  "tax_excluded_amount": "不含税金额，精确到分",
  "tax_amount": "税额，精确到分",
  "total_amount": "价税合计，精确到分",
  "profit_center": "从描述中提取，若无则用 PC-DEFAULT",
  "cost_center": "从描述中提取，若无则用 CC-DEFAULT"
}
```

如果用户没有提供某些字段，请根据上下文合理推断。如果完全无法推断金额，返回 null。
"""


# Supported business types and their display names
SUPPORTED_BUSINESS_TYPES = {
    "sales_revenue": "销售收入（销售商品或提供服务产生的收入）",
}


async def _parse_transaction_from_nl(message: str) -> dict | None:
    """Use LLM to extract intent, business_type and SalesTransaction from natural language.

    Returns a dict with keys:
      - intent: str — "business" or "chat"
      - reply: str | None — chat reply (when intent=chat)
      - business_type: str — the classified business type (when intent=business)
      - transaction: SalesTransaction | None — structured data
    """
    from openai import AsyncOpenAI
    import os

    base_url = os.environ.get(
        "PMDE_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3"
    )
    api_key = os.environ.get(
        "PMDE_API_KEY", "4fea2171-9079-434e-bdf5-d98a00db9363"
    )
    model_name = os.environ.get("PMDE_MODEL_NAME", "deepseek-v4-pro")

    today = date.today().strftime("%Y-%m-%d")
    user_prompt = (
        f"当前日期：{today}\n\n用户输入：{message}\n\n"
        "请先判断用户意图（intent），再进行后续处理。"
    )

    try:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        completion = await client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": NL_PARSE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )
        raw = completion.choices[0].message.content

        # Extract JSON
        json_str = raw.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        data = json.loads(json_str)

        intent = data.get("intent", "unknown")

        # Handle chat / question intent
        if intent == "chat":
            return {
                "intent": "chat",
                "reply": data.get("reply", "你好！我是 Ember，有什么可以帮你的吗？"),
                "business_type": None,
                "transaction": None,
            }

        # Handle rule query intent
        if intent == "rule_query":
            return {
                "intent": "rule_query",
                "rule_type": data.get("rule_type"),  # None if user didn't specify a type
                "reply": data.get("reply", ""),
                "business_type": None,
                "transaction": None,
            }

        # Handle business intent
        business_type = data.get("business_type", "other")

        # For non-sales_revenue types, return the classification without transaction
        if business_type != "sales_revenue":
            return {"intent": "business", "business_type": business_type, "transaction": None}

        # For sales_revenue, parse full transaction
        if data.get("tax_excluded_amount") is None or data.get("total_amount") is None:
            return {"intent": "business", "business_type": business_type, "transaction": None}

        from voucher_models import SalesTransaction

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

    except Exception as exc:
        logger.error("NL parse failed: %s", exc)
        return None


async def _parse_image_to_transaction(image_path: Path) -> dict | None:
    """Use multimodal LLM to extract business_type and SalesTransaction from an image.

    Returns a dict with keys:
      - business_type: str
      - transaction: SalesTransaction | None
    """
    from openai import AsyncOpenAI
    import os
    import base64

    base_url = os.environ.get(
        "PMDE_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3"
    )
    api_key = os.environ.get(
        "PMDE_API_KEY", "4fea2171-9079-434e-bdf5-d98a00db9363"
    )
    # Use a vision-capable model; fall back to the default if not set
    model_name = os.environ.get(
        "PMDE_VISION_MODEL_NAME",
        os.environ.get("PMDE_MODEL_NAME", "deepseek-v4-pro"),
    )

    today = date.today().strftime("%Y-%m-%d")

    # Read and base64-encode the image
    try:
        image_bytes = image_path.read_bytes()
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        # Determine MIME type
        ext = image_path.suffix.lower()
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }
        mime_type = mime_map.get(ext, "image/jpeg")
    except Exception as exc:
        logger.error("Failed to read image: %s", exc)
        return None

    try:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        completion = await client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": IMAGE_PARSE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{b64_image}",
                            },
                        },
                        {
                            "type": "text",
                            "text": f"当前日期：{today}\n\n请识别这张发票/单据图片，提取交易数据。",
                        },
                    ],
                },
            ],
            temperature=0.1,
        )
        raw = completion.choices[0].message.content

        # Extract JSON
        json_str = raw.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        data = json.loads(json_str)

        business_type = data.get("business_type", "other")

        # For non-sales_revenue types, return classification only
        if business_type != "sales_revenue":
            return {"business_type": business_type, "transaction": None}

        # For sales_revenue, parse full transaction
        if data.get("tax_excluded_amount") is None or data.get("total_amount") is None:
            return {"business_type": business_type, "transaction": None}

        from voucher_models import SalesTransaction

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

    except Exception as exc:
        logger.error("Image parse failed: %s", exc)
        return None


def _pdf_to_images(pdf_path: Path) -> list[tuple[bytes, str]]:
    """Convert each page of a PDF to PNG bytes. Returns list of (image_bytes, mime_type)."""
    import fitz

    doc = fitz.open(str(pdf_path))
    pages = []
    for page in doc:
        # Render at 200 DPI for good OCR quality
        pix = page.get_pixmap(dpi=200)
        pages.append((pix.tobytes("png"), "image/png"))
    doc.close()
    return pages


async def _parse_pdf_to_transaction(pdf_path: Path) -> dict | None:
    """Convert PDF pages to images, then use multimodal LLM to extract transaction data.

    For single-page PDFs, sends one image. For multi-page PDFs, sends all pages
    and asks the LLM to identify the most relevant one (typically the invoice page).

    Returns a dict with keys:
      - business_type: str
      - transaction: SalesTransaction | None
    """
    from openai import AsyncOpenAI
    import os
    import base64

    try:
        pages = _pdf_to_images(pdf_path)
    except Exception as exc:
        logger.error("Failed to convert PDF to images: %s", exc)
        return None

    if not pages:
        return None

    base_url = os.environ.get(
        "PMDE_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3"
    )
    api_key = os.environ.get(
        "PMDE_API_KEY", "4fea2171-9079-434e-bdf5-d98a00db9363"
    )
    model_name = os.environ.get(
        "PMDE_VISION_MODEL_NAME",
        os.environ.get("PMDE_MODEL_NAME", "deepseek-v4-pro"),
    )

    today = date.today().strftime("%Y-%m-%d")

    # Build image content blocks
    image_blocks = []
    for i, (img_bytes, mime_type) in enumerate(pages):
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        image_blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{b64}"},
        })

    # If multiple pages, add a note
    page_note = ""
    if len(pages) > 1:
        page_note = f"该PDF共{len(pages)}页，请识别其中包含发票/单据的页面并提取数据。"

    try:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        completion = await client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": IMAGE_PARSE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        *image_blocks,
                        {
                            "type": "text",
                            "text": f"当前日期：{today}\n\n请识别这张发票/单据，提取交易数据。{page_note}",
                        },
                    ],
                },
            ],
            temperature=0.1,
        )
        raw = completion.choices[0].message.content

        # Extract JSON
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

        from voucher_models import SalesTransaction

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

    except Exception as exc:
        logger.error("PDF parse failed: %s", exc)
        return None


# ── Serve static frontend ────────────────────────────────────────────────────

# Mount static files last so API routes take priority
app.mount("/", StaticFiles(directory=str(PROJECT_ROOT), html=True), name="static")


# ── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
