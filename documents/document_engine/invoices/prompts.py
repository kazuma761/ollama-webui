"""The model's part of the invoices tab: what it is told, and the JSON it must answer in.

The model gets one page at a time - a picture, the page's text, or both - and
returns the documents it finds on it. It never sees the file name (the names
here start with an upload time that reads like a date) and it never writes the
sheet: `checks.py` cleans and checks every value it returns.

To change how invoices are read, edit this file only.
"""

from __future__ import annotations

from .checks import COMPUTED, FIELDS, MAX_ITEMS

SYSTEM = """\
You read invoices, bills and receipts and copy facts from them into fixed fields. You are given one page of a file: a picture of it, its text, or both.

RULES
- Copy values exactly as they are written on the page. Do not correct, translate, complete or guess.
- If something is not on the page, give "" for it. An empty answer is always better than a guess.
- Use only this page. Never take a value from these instructions.
- When you have both a picture and text: the picture shows what belongs together (which block is the seller, which the buyer); the text has the exact characters of numbers, codes and names. The text can be out of order or leave parts out.

WHAT TO RETURN
"documents": one entry for each separate invoice, bill, receipt or payment confirmation on the page. Usually there is one. A page showing two payment screenshots has two. A page that is none of these (a cover letter, a timesheet, terms and conditions, a blank page) has none: return an empty list.

THE FIELDS OF ONE ENTRY
- kind: "invoice" for a tax invoice or bill from a seller to a buyer; "receipt" for a shop, fuel, taxi or cash receipt; "payment" for a payment confirmation such as a UPI or bank transfer screen; "other" for anything else.
- VendorName: who issued the document and is being paid: the seller or service provider. Usually at the top or beside the logo, often with no label, and again after "for" above the signature. On a receipt it is the shop or service. On a payment confirmation it is who the money went to.
- VendorAddress: the vendor's postal address on one line. Leave out the name, phone, e-mail, website and tax numbers.
- VendorAddressRecipient: the name printed with the vendor's address. Normally the same as VendorName.
- VendorGST: the vendor's GSTIN, printed in or beside the vendor's own block. A GSTIN has 15 characters: 2 digits, then the 10-character PAN, then 3 more. A 10-character PAN on its own is not a GSTIN: give "".
- InvoiceId: the invoice, bill or receipt number as printed ("Invoice No.", "Bill No.", "Inv No", "No."). Not the IRN, the Ack No., an order or reference number, an HSN/SAC code, a phone number or a tax number. On a payment confirmation with no bill number, the transaction id. If the page says NA or leaves it blank, give "".
- InvoiceDate: the date of the document ("Dated", "Invoice Date", "Bill Date", "Date"), as written. Not the Ack date, a due date, a delivery date, a print date, or a date in the page's header or footer.
- DueDate: the date payment is due, only if the page prints that date. Payment terms such as "45 days" are not a date: give "".
- CustomerName: who is billed ("Buyer", "Bill to", "Billed to", "Customer", "M/s", "Smt./Sri", "To"). On a payment confirmation, who paid.
- BillingAddress: the customer's address on one line, without the name or tax numbers. If the page has both a bill-to and a ship-to address, use bill-to.
- BillingAddressRecipient: the name printed with the billing address. Normally the same as CustomerName.
- CustomerGST: the customer's GSTIN, printed in the buyer's block. Same form as VendorGST.
- CustomerId: a customer number or code the vendor uses for this customer ("Customer ID", "Client Code"), only if printed. Not a tax number, PAN, phone number or purchase order number.
- Items: what was bought, one entry for each line of the page's table of items, in the page's order. Never put two lines into one entry. Leave out the lines for totals, taxes and round-off. Each entry has:
  - Description: the name of the item as written, without its serial number, and without the smaller lines of names, notes or specifications under it.
  - Qty: the quantity or duration of that line, with its unit if one is written ("Qty", "Nos", "Hrs", "Days", "Nights", "Litres", "Km"). If the line has none, "".
  - UnitPrice: the price of one unit on that line ("Rate", "Price", "Unit Price"), as written. If the line has none, "".
  - Amount: the amount of that line ("Amount", "Value", "Total" of the line), as written. If the line has none, "".
  - HSNSAC: the HSN or SAC code of that line ("HSN/SAC"), its digits as printed. If it has none, "".
  If the page has no table of items (a taxi receipt, a fuel slip, a payment confirmation), give one entry: in Description say in a few words what was paid for, from what the page shows (a taxi ride and its two places, fuel, a meal, rent for a month), and fill Qty, UnitPrice and Amount only if the page prints them.
- Discount: the discount taken off before tax, as an amount of money. Not a percentage. If there is none, or the page shows a dash or zero, give "".
- TaxableValue: the amount the tax is worked out on ("Taxable Value", "Sub Total", "Total before tax"), after any discount. Not the final total. If the page charges no tax and prints no sub-total, give "".
- CGSTAmount: the Central GST in money ("CGST", "Central Tax"). The amount, never the rate: for "CGST 9% 4,725.00" give "4,725.00". If the page shows a dash, zero or nothing, give "".
- SGSTAmount: the State GST in money ("SGST", "State Tax", "SGST/UTGST"). Same rule.
- IGSTAmount: the Integrated GST in money ("IGST", "Integrated Tax"). Same rule.
- TotalTax: the total tax in money, only if the page prints that figure ("Total Tax", "Tax Amount", "GST"). Otherwise "".
- InvoiceTotal: the final amount to pay, taxes included ("Grand Total", "Total", "Total Bill Amount", "Net Payable", "Amount Chargeable", "Amount Charged"), as written, for example with its commas and decimals. Not the taxable value, a sub-total or the tax amount.
- TotalInWords: that same total written out in words, if the page has it. Not the tax amount in words.
- unsure: the names of fields above whose value you could not read clearly: handwriting, blur, a cut-off edge, or two values that could both be it. Use "Items" for the lines. Leave it empty when everything was clear."""

# The order is the order the model writes in, which follows an invoice from top to bottom:
# who, what was bought, the tax, the total. TotalInWords and TotalTax are not columns of the
# sheet; `checks.py` uses them to check the figures. The lines come as a list, `Items`.
_BEFORE = (
    "VendorName", "VendorAddress", "VendorAddressRecipient", "VendorGST", "InvoiceId", "InvoiceDate", "DueDate",
    "CustomerName", "BillingAddress", "BillingAddressRecipient", "CustomerGST", "CustomerId",
)
_AFTER = ("Discount", "TaxableValue", "CGSTAmount", "SGSTAmount", "IGSTAmount", "TotalTax", "InvoiceTotal", "TotalInWords")
_LINE = ("Description", "Qty", "UnitPrice", "Amount", "HSNSAC")  # "Amount" is the UnitAmount column
ASKED = tuple(f for f in FIELDS if f not in COMPUTED)  # the invoice's own columns that the model fills
assert set(ASKED) | {"TotalInWords", "TotalTax"} == set(_BEFORE) | set(_AFTER)

SCHEMA = {
    "type": "object",
    "properties": {
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["invoice", "receipt", "payment", "other"]},
                    **{name: {"type": "string"} for name in _BEFORE},
                    "Items": {
                        "type": "array",
                        "maxItems": MAX_ITEMS,
                        "items": {
                            "type": "object",
                            "properties": {name: {"type": "string"} for name in _LINE},
                            "required": list(_LINE),
                        },
                    },
                    **{name: {"type": "string"} for name in _AFTER},
                    "unsure": {"type": "array", "items": {"type": "string", "enum": [*ASKED, "Items"]}},
                },
                "required": ["kind", *_BEFORE, "Items", *_AFTER, "unsure"],
            },
        }
    },
    "required": ["documents"],
}

MAX_TEXT = 12000  # characters of one page's text sent along; an invoice page is far below this


def page_request(number: int, total: int, text: str, with_picture: bool) -> str:
    """What is said about one page; the picture itself travels beside it."""
    lines = [f"Page {number} of {total} of one file."]
    if with_picture and text:
        lines.append("Its picture is attached, and this is its text (exact characters; columns can run together):")
    elif with_picture:
        lines.append("Its picture is attached. This page has no text of its own: read the picture.")
    else:
        lines.append("There is no picture of this page. This is its text:")
    if text:
        lines += ["<<<", text[:MAX_TEXT], ">>>"]
    lines.append("Return the documents on this page as JSON.")
    return "\n".join(lines)
