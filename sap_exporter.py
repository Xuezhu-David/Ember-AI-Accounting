"""Export voucher drafts to a simple SAP import-style CSV."""

import csv
from pathlib import Path

from voucher_models import Voucher


SAP_COLUMNS = [
    "BUKRS",
    "BLART",
    "BLDAT",
    "BUDAT",
    "XBLNR",
    "BKTXT",
    "BUZEI",
    "SHKZG",
    "HKONT",
    "ACCOUNT_NAME",
    "WRBTR",
    "WAERS",
    "KUNNR",
    "CUSTOMER_NAME",
    "MWSKZ",
    "PRCTR",
    "KOSTL",
    "ZUONR",
    "SGTXT",
]


def export_sap_csv(vouchers: list[Voucher], output_path: Path) -> None:
    """Write voucher lines to a SAP-style CSV file."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=SAP_COLUMNS)
        writer.writeheader()

        for voucher in vouchers:
            for line in voucher.lines:
                writer.writerow(
                    {
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
                    },
                )
