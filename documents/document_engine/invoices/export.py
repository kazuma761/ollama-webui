"""Writes the reviewed rows as an Excel file or a CSV. No model here.

Both have exactly the columns of `checks.COLUMNS`, in that order. In Excel the
dates are real dates and the total and confidence real numbers, so the sheet
sorts and sums. Values still marked "check" are shaded, and an optional second
sheet lists why.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from .checks import COLUMNS, FIELDS

WIDTHS = {
    "file": 34, "InvoiceId": 20, "InvoiceDate": 13, "DueDate": 13, "InvoiceTotal": 14, "VendorName": 30,
    "VendorAddress": 44, "CustomerName": 30, "CustomerId": 14, "BillingAddress": 44, "BillingAddressRecipient": 30,
    "VendorAddressRecipient": 30, "VendorGST": 19, "CustomerGST": 19, "confidence": 11,
}


def _cell(row: dict[str, Any], key: str) -> Any:
    if key == "file":
        return row.get("file") or ""
    if key == "confidence":
        return row.get("confidence")
    return (row.get("values") or {}).get(key) or ""


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe(text: str) -> str:
    """A cell starting with = or @ would be run by Excel as a formula when a CSV is opened.
    Invoices come from outside, so such a value gets a leading apostrophe."""
    risky = text[:1] in ("=", "@", "\t", "\r") or (text[:1] in ("+", "-") and _number(text) is None)
    return "'" + text if risky else text


def to_csv(rows: list[dict[str, Any]]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow([title for title, _ in COLUMNS])
    for row in rows:
        line = []
        for _, key in COLUMNS:
            value = _cell(row, key)
            line.append(f"{value:.2f}" if isinstance(value, float) else _safe(str(value if value is not None else "")))
        writer.writerow(line)
    return out.getvalue().encode("utf-8-sig")  # the BOM makes Excel read ₹ and accents correctly


def to_xlsx(rows: list[dict[str, Any]], date_format: str, with_checks: bool = False) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    sheet = book.active
    sheet.title = "Invoices"
    head = Font(bold=True)
    doubtful = PatternFill("solid", fgColor="FFF2CC")
    sheet.append([title for title, _ in COLUMNS])
    for index, (_, key) in enumerate(COLUMNS, 1):
        sheet.cell(1, index).font = head
        sheet.column_dimensions[get_column_letter(index)].width = WIDTHS[key]

    for number, row in enumerate(rows, 2):
        for index, (_, key) in enumerate(COLUMNS, 1):
            value, cell = _cell(row, key), sheet.cell(number, index)
            if key in ("InvoiceDate", "DueDate") and value:
                try:
                    cell.value, cell.number_format = datetime.strptime(value, date_format), "DD-MMM-YYYY"
                except ValueError:
                    cell.value, cell.data_type = value, "s"
            elif key == "InvoiceTotal" and _number(value) is not None:
                cell.value, cell.number_format = _number(value), "#,##0.00"
            elif key == "confidence":
                cell.value, cell.number_format = _number(value), "0.00"
            elif value != "":
                cell.value = str(value)
                cell.data_type = "s"  # always text: never a formula, and an id like 00123 keeps its zeros
            if key in ("VendorAddress", "BillingAddress"):
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            else:
                cell.alignment = Alignment(vertical="top")
            if (row.get("levels") or {}).get(key) == "check":
                cell.fill = doubtful
    sheet.freeze_panes = "B2"
    sheet.auto_filter.ref = sheet.dimensions

    if with_checks:
        checks = book.create_sheet("Checks")
        checks.append(["Invoice File Name", "Row", "Pages", "Kind", "Field", "Value", "Level", "Note"])
        for index, width in enumerate((34, 6, 8, 10, 24, 40, 8, 70), 1):
            checks.cell(1, index).font = head
            checks.column_dimensions[get_column_letter(index)].width = width
        for number, row in enumerate(rows, 2):
            pages = ", ".join(str(p) for p in row.get("pages") or [])
            for field in FIELDS:
                level, note = (row.get("levels") or {}).get(field, ""), (row.get("notes") or {}).get(field, "")
                if level == "check" or (note and level != "ok"):  # not the confirmations, only what needs a look
                    checks.append([row.get("file") or "", number, pages, row.get("kind") or "", field, None, level, note])
                    if value := str(_cell(row, field)):
                        cell = checks.cell(checks.max_row, 6)
                        cell.value, cell.data_type = value, "s"
        checks.freeze_panes = "A2"

    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
