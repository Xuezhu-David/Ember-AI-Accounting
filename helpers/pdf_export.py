"""PDF export for voucher records using ReportLab."""

import json
import logging
import os
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logger = logging.getLogger(__name__)

# ── Colours ───────────────────────────────────────────────────────────────────
C_BLUE    = colors.HexColor("#2563eb")
C_BLUE_LT = colors.HexColor("#eff6ff")
C_GRAY    = colors.HexColor("#6b7280")
C_BORDER  = colors.HexColor("#d1d5db")
C_ROW_ALT = colors.HexColor("#f9fafb")
C_BLACK   = colors.HexColor("#111827")
C_RED     = colors.HexColor("#dc2626")
C_GREEN   = colors.HexColor("#059669")

# ── Font ──────────────────────────────────────────────────────────────────────
_FONT_DONE = False

def _ensure_font():
    global _FONT_DONE
    if _FONT_DONE:
        return
    _FONT_DONE = True
    for path in [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode MS.ttf",
    ]:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("CJK", path))
                return
            except Exception:
                continue

def _f():
    _ensure_font()
    return "CJK" if "CJK" in pdfmetrics.getRegisteredFontNames() else "Helvetica"

# ── Helpers ───────────────────────────────────────────────────────────────────
_STATUS = {"draft": "草稿", "posted": "已过账", "reversed": "已冲销"}
_STATUS_COLOR = {"draft": C_GRAY, "posted": C_GREEN, "reversed": C_RED}
_DC = {"S": "借", "H": "贷"}

def _p(text, fn, size=9, color=C_BLACK, align=TA_LEFT, bold=False):
    name = fn + "-Bold" if bold and fn != "Helvetica" else fn
    return Paragraph(str(text or "—"), ParagraphStyle(
        "x", fontName=name, fontSize=size, textColor=color,
        alignment=align, leading=size * 1.3,
    ))

def _fmt(n):
    try:
        return f"¥{float(n):>14,.2f}"
    except Exception:
        return "—"

# ── Main ──────────────────────────────────────────────────────────────────────
async def generate_voucher_pdf(record: dict) -> bytes | None:
    try:
        fn = _f()
        voucher_data = json.loads(record.get("voucher_data") or "{}")
        rows         = voucher_data.get("rows", [])
        total_debit  = sum(float(r.get("debit",  0) or 0) for r in rows)
        total_credit = sum(float(r.get("credit", 0) or 0) for r in rows)
        status       = record.get("status", "draft")
        conf         = record.get("confidence", "")
        conf_str     = f"{float(conf)*100:.0f}%" if conf else "—"

        W = A4[0] - 28*mm   # usable width

        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=14*mm, rightMargin=14*mm,
                                topMargin=14*mm, bottomMargin=14*mm)
        story = []

        # ── 1. Title bar ───────────────────────────────────────────────────
        title_tbl = Table(
            [[_p("记  账  凭  证", fn, 18, C_BLACK, TA_CENTER)]],
            colWidths=[W],
        )
        title_tbl.setStyle(TableStyle([
            ("TOPPADDING",    (0,0),(0,0), 6),
            ("BOTTOMPADDING", (0,0),(0,0), 6),
            ("LINEBELOW",     (0,0),(0,0), 1.5, C_BLUE),
        ]))
        story.append(title_tbl)
        story.append(Spacer(1, 3*mm))

        # ── 2. Subtitle row: voucher_id left, status badge right ───────────
        s_color = _STATUS_COLOR.get(status, C_GRAY)
        s_label = _STATUS.get(status, status)
        sub_tbl = Table(
            [[
                _p(record.get("voucher_id",""), fn, 10, C_BLUE),
                _p(f"Ember AI 智能记账", fn, 8, C_GRAY, TA_CENTER),
                _p(s_label, fn, 9, s_color, TA_RIGHT),
            ]],
            colWidths=[W*0.4, W*0.3, W*0.3],
        )
        sub_tbl.setStyle(TableStyle([
            ("TOPPADDING",    (0,0),(-1,-1), 1),
            ("BOTTOMPADDING", (0,0),(-1,-1), 1),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ]))
        story.append(sub_tbl)
        story.append(Spacer(1, 4*mm))

        # ── 3. Header info grid (2 columns × N rows) ───────────────────────
        def kv(label, val, val_color=C_BLACK):
            return [
                _p(label, fn, 8, C_GRAY),
                _p(val or "—", fn, 9, val_color),
            ]

        hdr_rows = [
            kv("公司代码",  record.get("company_code",  "")),
            kv("凭证类型",  record.get("document_type", "")),
            kv("凭证日期",  record.get("document_date", "")),
            kv("过账日期",  record.get("posting_date",  "")),
            kv("参考号",    record.get("reference",     "")),
            kv("凭证文本",  record.get("header_text",   "")),
            kv("置信度",    conf_str),
            kv("创建人",    record.get("user_display_name", "")),
        ]

        # Pair into 2-column layout
        paired = []
        for i in range(0, len(hdr_rows), 2):
            left  = hdr_rows[i]
            right = hdr_rows[i+1] if i+1 < len(hdr_rows) else [_p("", fn), _p("", fn)]
            paired.append(left + [Spacer(4*mm, 1)] + right)

        cw = [22*mm, W/2-24*mm, 8*mm, 22*mm, W/2-28*mm]
        hdr_tbl = Table(paired, colWidths=cw)
        hdr_tbl.setStyle(TableStyle([
            ("FONTNAME",      (0,0),(-1,-1), fn),
            ("TOPPADDING",    (0,0),(-1,-1), 3),
            ("BOTTOMPADDING", (0,0),(-1,-1), 3),
            ("VALIGN",        (0,0),(-1,-1), "TOP"),
            ("LINEBELOW",     (0,-1),(-1,-1), 0.5, C_BORDER),
        ]))
        story.append(hdr_tbl)
        story.append(Spacer(1, 5*mm))

        # ── 4. Journal entries table ───────────────────────────────────────
        story.append(_p("会 计 分 录", fn, 9, C_GRAY, TA_LEFT))
        story.append(Spacer(1, 2*mm))

        # Column widths
        cw2 = [20*mm, 50*mm, 12*mm, 30*mm, 30*mm, 10*mm, W-152*mm]

        head = [[
            _p("科目代码", fn, 8, colors.white, TA_CENTER),
            _p("科目名称", fn, 8, colors.white, TA_LEFT),
            _p("借/贷",    fn, 8, colors.white, TA_CENTER),
            _p("借方金额", fn, 8, colors.white, TA_RIGHT),
            _p("贷方金额", fn, 8, colors.white, TA_RIGHT),
            _p("税码",     fn, 8, colors.white, TA_CENTER),
            _p("摘要",     fn, 8, colors.white, TA_LEFT),
        ]]

        line_rows = []
        for i, r in enumerate(rows):
            dc    = r.get("dc", "")
            debit  = float(r.get("debit",  0) or 0)
            credit = float(r.get("credit", 0) or 0)
            bg = colors.white if i % 2 == 0 else C_ROW_ALT
            line_rows.append([
                _p(r.get("account_code",""), fn, 8, C_BLACK, TA_CENTER),
                _p(r.get("account_name",""), fn, 8, C_BLACK, TA_LEFT),
                _p(_DC.get(dc, dc),           fn, 8, C_BLUE  if dc=="S" else C_RED, TA_CENTER),
                _p(f"¥{debit:,.2f}"  if debit  else "", fn, 8, C_BLACK, TA_RIGHT),
                _p(f"¥{credit:,.2f}" if credit else "", fn, 8, C_BLACK, TA_RIGHT),
                _p(r.get("tax_code",""),      fn, 8, C_GRAY,  TA_CENTER),
                _p(r.get("text") or r.get("assignment",""), fn, 8, C_BLACK, TA_LEFT),
            ])

        # Total row
        line_rows.append([
            _p("", fn),
            _p("合　计", fn, 9, C_BLACK, TA_RIGHT),
            _p("", fn),
            _p(f"¥{total_debit:,.2f}",  fn, 9, C_BLACK, TA_RIGHT),
            _p(f"¥{total_credit:,.2f}", fn, 9, C_BLACK, TA_RIGHT),
            _p("", fn),
            _p("", fn),
        ])

        tbl_data = head + line_rows
        n = len(tbl_data)
        nl = len(line_rows)

        entry_tbl = Table(tbl_data, colWidths=cw2, repeatRows=1)
        style_cmds = [
            ("FONTNAME",       (0,0),(-1,-1), fn),
            ("FONTSIZE",       (0,0),(-1,-1), 8),
            ("TOPPADDING",     (0,0),(-1,-1), 4),
            ("BOTTOMPADDING",  (0,0),(-1,-1), 4),
            ("LEFTPADDING",    (0,0),(-1,-1), 4),
            ("RIGHTPADDING",   (0,0),(-1,-1), 4),
            ("VALIGN",         (0,0),(-1,-1), "MIDDLE"),
            # Header row
            ("BACKGROUND",     (0,0),(-1,0),  C_BLUE),
            ("ROWBACKGROUNDS", (0,1),(-1,n-2), [colors.white, C_ROW_ALT]),
            # Total row
            ("BACKGROUND",     (0,-1),(-1,-1), C_BLUE_LT),
            ("FONTSIZE",       (0,-1),(-1,-1), 9),
            ("LINEABOVE",      (0,-1),(-1,-1), 1, C_BLUE),
            # Grid
            ("GRID",           (0,0),(-1,-1), 0.3, C_BORDER),
            ("LINEBELOW",      (0,0),(-1,0),  1.2, C_BLUE),
            ("BOX",            (0,0),(-1,-1), 0.8, C_BORDER),
        ]
        entry_tbl.setStyle(TableStyle(style_cmds))
        story.append(entry_tbl)
        story.append(Spacer(1, 6*mm))

        # ── 5. Balance check ───────────────────────────────────────────────
        diff = abs(total_debit - total_credit)
        if diff < 0.01:
            bal_text = "✓ 借贷平衡"
            bal_color = C_GREEN
        else:
            bal_text = f"⚠ 借贷不平衡，差额 ¥{diff:,.2f}"
            bal_color = C_RED
        story.append(_p(bal_text, fn, 8, bal_color, TA_RIGHT))
        story.append(Spacer(1, 8*mm))

        # ── 6. Signature row ───────────────────────────────────────────────
        sig_data = [[
            _p("制单人：________________", fn, 8, C_GRAY, TA_CENTER),
            _p("审核人：________________", fn, 8, C_GRAY, TA_CENTER),
            _p("记账人：________________", fn, 8, C_GRAY, TA_CENTER),
            _p("主管：__________________", fn, 8, C_GRAY, TA_CENTER),
        ]]
        sig_tbl = Table(sig_data, colWidths=[W/4]*4)
        sig_tbl.setStyle(TableStyle([
            ("TOPPADDING",    (0,0),(-1,-1), 6),
            ("BOTTOMPADDING", (0,0),(-1,-1), 6),
            ("LINEBEFORE",    (1,0),(3,0), 0.5, C_BORDER),
        ]))
        story.append(sig_tbl)
        story.append(Spacer(1, 4*mm))
        story.append(HRFlowable(width=W, thickness=0.5, color=C_BORDER))
        story.append(Spacer(1, 2*mm))

        # ── 7. Footer ──────────────────────────────────────────────────────
        created = record.get("created_at","")
        posted  = record.get("posted_at","")
        by      = record.get("user_display_name","")
        footer_parts = [f"创建：{created}  创建人：{by}"]
        if posted:
            footer_parts.append(f"过账：{posted}  过账人：{record.get('posted_by_name','')}")
        story.append(_p("  |  ".join(footer_parts), fn, 7, C_GRAY, TA_CENTER))
        story.append(Spacer(1, 1*mm))
        story.append(_p("本凭证由 Ember AI 自动生成，科目编码仅供参考，请以企业实际账务为准。", fn, 7, C_GRAY, TA_CENTER))

        doc.build(story)
        return buf.getvalue()

    except Exception:
        logger.exception("Failed to generate PDF for voucher %s", record.get("voucher_id"))
        return None
