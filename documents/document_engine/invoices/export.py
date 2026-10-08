"""Writes the reviewed rows as an Excel file or a CSV. No model here.

Both have exactly the columns of `checks.COLUMNS`, in that order, and a row for
each line of each invoice: an invoice of seven items is seven rows. The
invoice's own fields are repeated on each; its totals and taxes are written on
its first row only, unless asked otherwise, so that a column still adds up. In
Excel the dates are real dates and the amounts and the confidence real numbers. Values still marked "check" are shaded, and an optional second
sheet lists why.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from .checks import AMOUNTS, COLUMNS, FIELDS, ITEM_FIELDS, KIND_NAMES, NUMBERS

WIDTHS = {
    "file": 34, "InvoiceId": 20, "InvoiceDate": 13, "DueDate": 13, "InvoiceTotal": 14, "VendorName": 30,
    "VendorAddress": 44, "CustomerName": 30, "CustomerId": 14, "BillingAddress": 44, "BillingAddressRecipient": 30,
    "VendorAddressRecipient": 30, "VendorGST": 19, "CustomerGST": 19, "confidence": 11,
    "Description": 44, "Qty": 12, "UnitPrice": 12, "UnitAmount": 13, "Discount": 12, "TaxableValue": 14,
    "CGSTAmount": 13, "SGSTAmount": 13, "IGSTAmount": 13, "TotalTaxAmount": 15, "HSNSAC": 12, "Currency": 9, "kind": 13,
}


def _lines(rows: list[dict[str, Any]]):
    """Every line of every invoice, as (invoice, line, whether it is the invoice's first)."""
    for row in rows:
        for number, item in enumerate(row.get("items") or [{}]):
            yield row, item, number == 0


def _cell(row: dict[str, Any], item: dict[str, Any], first: bool, key: str, repeat_totals: bool) -> Any:
    if key == "file":
        return row.get("file") or ""
    if key == "confidence":
        return row.get("confidence")
    if key == "kind":
        return KIND_NAMES.get(row.get("kind") or "", "")
    if key in ITEM_FIELDS:
        return (item.get("values") or {}).get(key) or ""
    if key in AMOUNTS and not first and not repeat_totals:
        return ""  # the invoice's own figures stand once, or a sum of the column would count them again
    return (row.get("values") or {}).get(key) or ""


def _level(row: dict[str, Any], item: dict[str, Any], first: bool, key: str) -> str:
    if key in ITEM_FIELDS:
        return (item.get("levels") or {}).get(key, "")
    return (row.get("levels") or {}).get(key, "") if first else ""  # shaded once, on the invoice's first row


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


def to_csv(rows: list[dict[str, Any]], repeat_totals: bool = False) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow([title for title, _ in COLUMNS])
    for row, item, first in _lines(rows):
        line = []
        for _, key in COLUMNS:
            value = _cell(row, item, first, key, repeat_totals)
            line.append(f"{value:.2f}" if isinstance(value, (int, float)) else _safe(str(value if value is not None else "")))
        writer.writerow(line)
    return out.getvalue().encode("utf-8-sig")  # the BOM makes Excel read ₹, accents and other scripts correctly


def to_xlsx(rows: list[dict[str, Any]], date_format: str, with_checks: bool = False, repeat_totals: bool = False) -> bytes:
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

    starts: dict[int, int] = {}  # where each invoice's first row is, for the Checks sheet
    for number, (row, item, first) in enumerate(_lines(rows), 2):
        if first:
            starts[id(row)] = number
        for index, (_, key) in enumerate(COLUMNS, 1):
            value, cell = _cell(row, item, first, key, repeat_totals), sheet.cell(number, index)
            if key in ("InvoiceDate", "DueDate") and value:
                try:
                    cell.value, cell.number_format = datetime.strptime(value, date_format), "DD-MMM-YYYY"
                except ValueError:
                    cell.value, cell.data_type = value, "s"
            elif key in NUMBERS and _number(value) is not None:
                cell.value, cell.number_format = _number(value), "#,##0.00"
            elif key == "confidence":
                cell.value, cell.number_format = _number(value), "0.00"
            elif value != "":
                cell.value = str(value)
                cell.data_type = "s"  # always text: never a formula, and an id like 00123 keeps its zeros
            wrap = key in ("VendorAddress", "BillingAddress", "Description")
            cell.alignment = Alignment(wrap_text=wrap, vertical="top")
            if _level(row, item, first, key) == "check":
                cell.fill = doubtful
    sheet.freeze_panes = "B2"
    sheet.auto_filter.ref = sheet.dimensions

    if with_checks:
        checks = book.create_sheet("Checks")
        checks.append(["Invoice File Name", "Row", "Pages", "Kind", "Field", "Value", "Level", "Note"])
        for index, width in enumerate((34, 6, 8, 10, 24, 40, 8, 70), 1):
            checks.cell(1, index).font = head
            checks.column_dimensions[get_column_letter(index)].width = width
        for row in rows:
            pages = ", ".join(str(p) for p in row.get("pages") or [])
            for field in (*FIELDS, *ITEM_FIELDS):
                level, note = (row.get("levels") or {}).get(field, ""), (row.get("notes") or {}).get(field, "")
                # Not the confirmations, only what needs a look; a line column only when there is something to say.
                if (note and level != "ok") or (level == "check" and field in FIELDS):
                    checks.append([row.get("file") or "", starts.get(id(row)), pages, row.get("kind") or "", field, None, level, note])
                    if field in FIELDS and (value := str((row.get("values") or {}).get(field) or "")):
                        cell = checks.cell(checks.max_row, 6)
                        cell.value, cell.data_type = value, "s"
        checks.freeze_panes = "A2"

    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
