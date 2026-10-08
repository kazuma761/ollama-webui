"""Cleans what the model read off an invoice, checks it, and scores the confidence. No model here.

A model reading a page can misread a character or pick the wrong block, and it
cannot tell you when it has. So nothing it returns is taken on trust: every
value gets one of four levels, and the row's Confidence is worked out from them.

  ok      confirmed by a check: the value is in the file's own text, a GSTIN's
          check digit is right, the total matches the amount in words, or
          taxable value + tax adds up to the total
  read    read from the page and well-formed, but nothing to confirm it against
          (a scan or a photo has no text of its own)
  check   something is off: a failed check, a value the text does not contain,
          or one the model itself marked as hard to read. A person should look.
  empty   not on the page

The Confidence column is the weighted average of those levels (ok 1.0, read 0.8,
check 0.35, an expected field that is missing 0.3). It is a score from checks,
not a probability.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

# An invoice has fields of its own (one value for the whole invoice) and lines: what was
# bought, one line for each thing. Every line is a row of the sheet. The invoice's fields are
# repeated on each of its rows; its totals and taxes are written on the first only, so that
# adding up a column of the sheet still gives the right sum.
FIELDS = (
    "InvoiceId", "InvoiceDate", "DueDate", "InvoiceTotal", "VendorName", "VendorAddress", "CustomerName",
    "CustomerId", "BillingAddress", "BillingAddressRecipient", "VendorAddressRecipient", "VendorGST", "CustomerGST",
    "Discount", "TaxableValue", "CGSTAmount", "SGSTAmount", "IGSTAmount", "TotalTaxAmount", "Currency",
)
ITEM_FIELDS = ("Description", "Qty", "UnitPrice", "UnitAmount", "HSNSAC")  # of one line
COMPUTED = ("TotalTaxAmount", "Currency")  # worked out here from what was read; the model is not asked for them
AMOUNTS = ("InvoiceTotal", "Discount", "TaxableValue", "CGSTAmount", "SGSTAmount", "IGSTAmount", "TotalTaxAmount")
NUMBERS = (*AMOUNTS, "UnitPrice", "UnitAmount")  # written as numbers in the sheet
TAXES = ("CGSTAmount", "SGSTAmount", "IGSTAmount")
COLUMNS = (
    ("Invoice File Name", "file"), ("InvoiceId", "InvoiceId"), ("Invoice Date", "InvoiceDate"), ("DueDate", "DueDate"),
    ("InvoiceTotal", "InvoiceTotal"), ("VendorName", "VendorName"), ("VendorAddress", "VendorAddress"),
    ("CustomerName", "CustomerName"), ("CustomerId", "CustomerId"), ("BillingAddress", "BillingAddress"),
    ("BillingAddressRecipient", "BillingAddressRecipient"), ("VendorAddressRecipient", "VendorAddressRecipient"),
    ("VendorGST", "VendorGST"), ("CustomerGST", "CustomerGST"),
    ("Description", "Description"), ("Qty", "Qty"), ("UnitPrice", "UnitPrice"), ("UnitAmount", "UnitAmount"),
    ("Discount", "Discount"), ("TaxableValue", "TaxableValue"), ("CGSTAmount", "CGSTAmount"), ("SGSTAmount", "SGSTAmount"),
    ("IGSTAmount", "IGSTAmount"), ("TotalTaxAmount", "TotalTaxAmount"), ("HSNSAC", "HSNSAC"), ("Currency", "Currency"),
    ("DocumentType", "kind"), ("Confidence", "confidence"),
)
KIND_NAMES = {"invoice": "Invoice", "receipt": "Receipt", "payment": "Payment"}  # the DocumentType column
KINDS = ("invoice", "receipt", "payment")  # what becomes a row; anything else on a page is skipped

WEIGHT = {
    "InvoiceTotal": 3, "InvoiceId": 2, "InvoiceDate": 2, "VendorName": 2, "CustomerName": 1.5, "VendorGST": 1.5,
    "CustomerGST": 1.5, "VendorAddress": 1, "BillingAddress": 1, "DueDate": 1, "CustomerId": 1,
    "BillingAddressRecipient": 0.5, "VendorAddressRecipient": 0.5,
    "TaxableValue": 1.5, "CGSTAmount": 1, "SGSTAmount": 1, "IGSTAmount": 1, "TotalTaxAmount": 1, "Description": 1,
    "Discount": 0.5, "Qty": 0.5, "HSNSAC": 0.5, "UnitPrice": 0.5, "UnitAmount": 1,
}  # Currency has no weight: it is read off the currency sign and says nothing about the reading
SCORE = {"ok": 1.0, "read": 0.8, "check": 0.35}
MISSING = 0.3
# What a document of each kind should have. A fuel receipt has no buyer and often no number.
EXPECTED = {
    "invoice": ("InvoiceId", "InvoiceDate", "InvoiceTotal", "VendorName", "CustomerName"),
    "receipt": ("InvoiceDate", "InvoiceTotal", "VendorName"),
    "payment": ("InvoiceDate", "InvoiceTotal", "VendorName"),
}
ROUND_OFF = 1.01  # invoices round the total to the rupee, so sums may differ by this much
MAX_DESCRIPTION = 300
MAX_ITEMS = 60  # lines kept from one page
OLD_YEARS = 3  # an invoice dated longer ago than this is more likely a misread year
NOT_AN_ANSWER = {
    "", "n/a", "na", "n.a.", "nil", "none", "null", "unknown", "not found", "not provided", "not available",
    "not specified", "not mentioned", "not applicable", "-", "--", "---", "tbd", "blank", "empty",
}


def _plain(text: str) -> str:
    """Lower case, punctuation and spacing ignored - for telling whether a value appears in a text."""
    return " ".join(re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", text).lower()).split())


# ── GSTIN ────────────────────────────────────────────────────────────────────
# 15 characters: 2 digits of state code, the 10-character PAN, an entity number,
# "Z", and a check digit worked out from the first 14. The check digit is what
# makes a GSTIN read off a photo trustworthy: one misread character and it fails.

_CODE = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN = re.compile(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]")
_PAN = re.compile(r"[A-Z]{5}\d{4}[A-Z]")
_DIGIT_AT = {0, 1, 7, 8, 9, 10}
_LETTER_AT = {2, 3, 4, 5, 6, 11}
_AS_DIGIT = str.maketrans("OQDILSBZG", "000115826")  # letters that look like digits
_AS_LETTER = str.maketrans("015826", "OISBZG")
_LOOK_ALIKE = {"O": "0", "0": "O", "I": "1", "1": "I", "S": "5", "5": "S", "B": "8", "8": "B", "Z": "2", "2": "Z", "G": "6", "6": "G", "Q": "0", "D": "0"}


def gstin_check_digit(first14: str) -> str:
    total = 0
    for index, char in enumerate(first14):
        quotient, remainder = divmod(_CODE.index(char) * (2 if index % 2 else 1), 36)
        total += quotient + remainder
    return _CODE[(36 - total % 36) % 36]


def gstin_valid(value: str) -> bool:
    return bool(_GSTIN.fullmatch(value)) and gstin_check_digit(value[:14]) == value[14]


def gstins_in(text: str) -> list[str]:
    """Every valid GSTIN printed in a text. Some invoices print one with a space inside it."""
    upper = text.upper()
    found = _GSTIN.findall(upper) + _GSTIN.findall(re.sub(r"(?<=[0-9A-Z]) {1,3}(?=[0-9A-Z])", "", upper))
    return list(dict.fromkeys(m for m in found if gstin_valid(m)))


def read_gstin(value: str, text: str = "") -> tuple[str, str, str]:
    """A GSTIN as the model gave it -> (value, level, note)."""
    raw = re.sub(r"[^0-9A-Za-z]", "", value).upper()
    if len(raw) > 15 and (found := _GSTIN.search(raw)):  # the label came along: "GSTIN29ABC..."
        raw = found.group(0)
    if not raw:
        return "", "empty", ""
    if _PAN.fullmatch(raw):
        return "", "empty", "The page gives a PAN here, not a GSTIN."
    if len(raw) != 15:
        return raw, "check", f"A GSTIN has 15 characters; this has {len(raw)}."
    if gstin_valid(raw):
        return raw, "ok", ""
    # O and 0, I and 1, S and 5 look alike. Each position of a GSTIN is known to be a digit
    # or a letter, so those can be put right - and the check digit says whether that was it.
    fixed = "".join(
        char.translate(_AS_DIGIT) if index in _DIGIT_AT else char.translate(_AS_LETTER) if index in _LETTER_AT else char
        for index, char in enumerate(raw)
    )
    if fixed != raw and gstin_valid(fixed):
        return fixed, "ok", f"Read as {raw}; look-alike characters corrected, and the check digit confirms it."
    # The same number, correctly spelled, may be in the file's own text.
    near = [g for g in gstins_in(text) if sum(a != b for a, b in zip(g, raw)) <= 2]
    if len(near) == 1:
        return near[0], "ok", f"Read as {raw}; corrected from the file's text."
    # The entity number and the check digit itself can be a digit or a letter. If swapping one
    # look-alike there makes the check digit right, that is very likely what was printed - but
    # it is a guess, so it is not marked as confirmed.
    swaps = [
        fixed[:at] + _LOOK_ALIKE[fixed[at]] + fixed[at + 1:] for at in (12, 14) if fixed[at] in _LOOK_ALIKE
    ]
    swaps = [candidate for candidate in swaps if gstin_valid(candidate)]
    if len(swaps) == 1:
        return swaps[0], "read", f"Read as {raw}; changed one look-alike character so the check digit is right. Compare with the invoice."
    if _GSTIN.fullmatch(raw):
        return raw, "check", "The check digit does not match: one character is probably misread."
    return raw, "check", "This does not have the form of a GSTIN."


# ── Dates and amounts ────────────────────────────────────────────────────────

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")


def _month(word: str) -> int | None:
    word = "sep" if word == "sept" else word
    return next((i for i, name in enumerate(_MONTHS, 1) if len(word) >= 3 and name.startswith(word)), None)


def parse_date(value: str) -> date | None:
    """A date as written on an invoice. Numbers are read day first, as Indian invoices write them."""
    text = re.sub(r"\d{1,2}:\d{2}(:\d{2})?\s*([ap]\.?m\.?)?", " ", value.lower())  # a time of day
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text)
    parts: list[tuple[str, int, int]] = []  # ("n", number, digits) or ("m", month, 0)
    for token in re.findall(r"[a-z]+|\d+", text):
        if token.isdigit():
            parts.append(("n", int(token), len(token)))
        elif (month := _month(token)) is not None:
            parts.append(("m", month, 0))
    shape = "".join(kind for kind, _, _ in parts)
    if shape == "nnn":
        (_, a, digits), (_, b, _), (_, c, _) = parts
        year, month, day = (a, b, c) if digits == 4 else (c, b, a)
        if month > 12 >= day and digits != 4:  # 03/31/2026: month first
            month, day = day, month
    elif shape == "nmn":
        day, month, year = parts[0][1], parts[1][1], parts[2][1]
    elif shape == "mnn":
        month, day, year = parts[0][1], parts[1][1], parts[2][1]
    else:
        return None
    year += 2000 if year < 100 else 0
    try:
        return date(year, month, day) if 2000 <= year <= 2100 else None
    except ValueError:
        return None


def parse_money(written: str) -> float | None:
    """An amount beside which a rate may be written: "9% 4,725.00" -> 4725.0, "9%" alone -> None."""
    return parse_amount(re.sub(r"\d+(?:\.\d+)?\s*%", " ", written))


_CURRENCIES = (
    ("INR", r"₹|\bINR\b|\bRs\b|\bRupees?\b"), ("SGD", r"(?<![A-Za-z])S\$|\bSGD\b"), ("USD", r"US\$|\bUSD\b|\bdollars?\b|(?<![A-Za-z])\$"),
    ("EUR", r"€|\bEUR\b|\beuros?\b"), ("GBP", r"£|\bGBP\b|\bpounds? sterling\b"), ("AED", r"\bAED\b|\bdirhams?\b"),
)


def currency_of(*written: str) -> str:
    """The currency named by the first of these texts that names exactly one: "₹ 1,17,977.58" -> INR."""
    for text in written:
        found = [code for code, pattern in _CURRENCIES if re.search(pattern, text, re.IGNORECASE)]
        if len(found) == 1:
            return found[0]
    return ""


def parse_amount(value: str) -> float | None:
    """"₹ 1,17,977.58", "Rs.1000.00", "6451/-" -> a number."""
    found = re.search(r"-?\d[\d,]*(?:\.\d+)?", value)
    if not found:
        return None
    try:
        return float(found.group(0).replace(",", ""))
    except ValueError:
        return None


_SMALL = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
)}
_TENS = {w: i * 10 for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)}
_TENS["fourty"] = 40
_SCALE = {"thousand": 1_000, "lakh": 100_000, "lac": 100_000, "million": 1_000_000, "crore": 10_000_000}
_FILLER = {"inr", "rs", "rupee", "rupees", "only", "and", "indian", "amount", "in", "words", "total", "of", "the"}


def _number_words(words: list[str]) -> int | None:
    total = current = 0
    for word in words:
        word = word[:-1] if word.endswith("s") and word[:-1] in _SCALE else word
        if word in _SMALL:
            current += _SMALL[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = max(current, 1) * 100
        elif word in _SCALE:
            total += max(current, 1) * _SCALE[word]
            current = 0
        else:
            return None  # a word that is not a number: do not guess
    return total + current


def words_amount(text: str) -> float | None:
    """"INR Sixty One Thousand Nine Hundred Fifty Only" -> 61950.0, or None when it is not clear."""
    words = [w for w in re.findall(r"[a-z]+", text.lower()) if w not in _FILLER]
    paise = 0
    for marker in ("paise", "paisa"):
        if marker in words:
            at = words.index(marker)
            before, after = words[:at], words[at + 1:]
            if after:  # "... and paise fifty eight"
                part, words = after, before
            else:  # "... seventy seven and fifty eight paise": the last number of at most two words
                cut = len(before) - (2 if len(before) >= 2 and before[-2] in _TENS else 1)
                part, words = before[cut:], before[:cut]
            paise = _number_words(part) or 0
            break
    whole = _number_words(words) if words else None
    return None if whole is None or not words else whole + paise / 100


# ── One record ───────────────────────────────────────────────────────────────

def _text(value: Any, one_line: bool = True) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip().strip('"“”').strip()
    if value.lower() in NOT_AN_ANSWER:
        return ""
    if one_line:
        value = re.sub(r"\s*[\r\n]+\s*", ", ", value)
        value = re.sub(r"\s+", " ", value)
        value = re.sub(r"(,\s*){2,}", ", ", value).strip(" ,")
    return value


def _in_text(field: str, value: str, text: str) -> bool:
    """Whether the file's own text contains this value."""
    if field in ("VendorAddress", "BillingAddress", "Description"):  # may be reworded or shortened: most words must be there
        words = [w for w in _plain(value).split() if len(w) > 1]
        have = set(_plain(text).split())
        return bool(words) and sum(w in have for w in words) / len(words) >= 0.85
    return bool(_plain(value)) and _plain(value) in _plain(text)


def _amount_in_text(amount: float, text: str) -> bool:
    flat = re.sub(r"(?<=\d),(?=\d)", "", text)
    whole = int(amount)
    forms = {f"{amount:.2f}"} | ({str(whole)} if amount == whole else set())
    return any(re.search(rf"(?<![\d.]){re.escape(form)}(?!\d)", flat) for form in forms)


# Lines of the item table that are not things bought: the model sometimes lists them as items.
_NOT_AN_ITEM = re.compile(
    r"(sub ?total|grand total|net (amount|total|payable)|total( amount| value)?|taxable (value|amount)|"
    r"(c|s|i|ut)gst( .*)?|(central|state|integrated) tax( .*)?|round(ed|ing)? ?off|amount in words.*)"
)


def _is_item(item: dict[str, Any]) -> bool:
    return any(item["values"].values())


def read_items(raw: dict[str, Any], text: str) -> list[dict[str, Any]]:
    """The lines of one document as the model returned them -> cleaned, each value with a level.
    There is always at least one line, empty if nothing was bought item by item."""
    listed = raw.get("Items")
    if not isinstance(listed, list):  # an answer without lines: one description for the lot
        listed = [{key: raw.get(key) for key in ("Description", "Qty", "HSNSAC")}]
    items: list[dict[str, Any]] = []
    for entry in listed[:MAX_ITEMS]:
        if not isinstance(entry, dict):
            continue
        values = {
            "Description": _text(entry.get("Description"))[:MAX_DESCRIPTION].rstrip(" ;,"),
            "Qty": _text(entry.get("Qty")),
            "UnitPrice": _text(entry.get("UnitPrice")),
            "UnitAmount": _text(entry.get("Amount", entry.get("UnitAmount"))),
            "HSNSAC": "; ".join(dict.fromkeys(re.findall(r"\d{4,8}", _text(entry.get("HSNSAC"))))),
        }
        if not any(values.values()) or _NOT_AN_ITEM.fullmatch(_plain(values["Description"])):
            continue
        levels = {f: "read" if values[f] else "empty" for f in ITEM_FIELDS}
        figures: dict[str, float] = {}
        for f in ("UnitPrice", "UnitAmount"):
            if not values[f]:
                continue
            amount = parse_money(values[f])
            if amount is None:
                levels[f] = "check"  # left as written
                continue
            figures[f] = amount
            values[f] = f"{amount:.2f}"
            if text and _amount_in_text(amount, text):
                levels[f] = "ok"
        if text:  # found in the file's own text; not finding it says little, the sums below say more
            if values["Description"] and _in_text("Description", values["Description"], text):
                levels["Description"] = "ok"
            if values["Qty"] and _in_text("Qty", values["Qty"], text):
                levels["Qty"] = "ok"
            if values["HSNSAC"] and all(re.search(rf"(?<!\d){code}(?!\d)", text) for code in values["HSNSAC"].split("; ")):
                levels["HSNSAC"] = "ok"
        item: dict[str, Any] = {"values": values, "levels": levels}
        # Quantity x unit price = amount: a check on a line that needs nothing but the line.
        count = parse_amount(values["Qty"]) if values["Qty"] else None
        if count is not None and len(figures) == 2:
            if abs(count * figures["UnitPrice"] - figures["UnitAmount"]) <= ROUND_OFF:
                levels["Qty"] = levels["UnitPrice"] = levels["UnitAmount"] = "ok"
            else:
                levels["UnitAmount"] = "check"
                item["note"] = f"{count:g} x {figures['UnitPrice']:,.2f} = {count * figures['UnitPrice']:,.2f}, not {figures['UnitAmount']:,.2f}"
        items.append(item)
    return items or [{"values": {f: "" for f in ITEM_FIELDS}, "levels": {f: "empty" for f in ITEM_FIELDS}}]


def finish(row: dict[str, Any]) -> dict[str, Any]:
    """The last step for a row, once the pages of its invoice are together: do the lines add
    up to the invoice, what level has each line column, and the Confidence."""
    values, levels, notes, items = row["values"], row["levels"], row["notes"], row["items"]
    real = [item for item in items if _is_item(item)]
    amounts = [parse_amount(item["values"]["UnitAmount"]) if item["values"]["UnitAmount"] else None for item in real]
    figure = {f: parse_amount(values[f]) if values[f] else None for f in ("InvoiceTotal", "TaxableValue", "TotalTaxAmount", "Discount")}
    total, base, tax, discount = figure["InvoiceTotal"], figure["TaxableValue"], figure["TotalTaxAmount"] or 0.0, figure["Discount"] or 0.0
    said = []
    if real and total is not None and all(amount is not None for amount in amounts):
        # The lines add up to the total (prices with tax in them, or no tax), to the total
        # before tax, or to the taxable value - with or without the discount taken off.
        summed = sum(amounts)
        against_total = (total, total - tax, total - tax + discount)
        against_base = () if base is None else (base, base + discount)
        if any(abs(summed - target) <= ROUND_OFF for target in (*against_total, *against_base)):
            for item in real:
                if "note" not in item:
                    item["levels"]["UnitAmount"] = "ok"
            if levels["InvoiceTotal"] == "read" and any(abs(summed - target) <= ROUND_OFF for target in against_total):
                levels["InvoiceTotal"], notes["InvoiceTotal"] = "ok", "The amounts of the lines add up to it."
        else:
            said.append(
                f"The amounts of the lines add up to {summed:,.2f}, but the total is {total:,.2f}"
                + ("" if base is None else f" and the taxable value {base:,.2f}") + ": a line is missing or misread."
            )
            for item in real:
                if item["levels"]["UnitAmount"] == "read":
                    item["levels"]["UnitAmount"] = "check"
    wrong = [f"line {number}: {item.pop('note')}" for number, item in enumerate(real, 1) if "note" in item]
    if wrong:
        said.append("Quantity x unit price is not the amount on " + "; ".join(wrong[:3]) + (f"; and {len(wrong) - 3} more" if len(wrong) > 3 else "") + ".")
    if said:
        notes["UnitAmount"] = " ".join(said)
    if row.pop("items_unsure", False):
        notes["Description"] = "The model could not read the lines clearly."
        for item in real:
            for f in ITEM_FIELDS:
                if item["levels"][f] == "read":
                    item["levels"][f] = "check"
    for f in ITEM_FIELDS:  # a column is as doubtful as its most doubtful line
        found = [item["levels"][f] for item in real if item["values"][f]]
        levels[f] = min(found, key=lambda level: SCORE.get(level, 0)) if found else "empty"
    row["confidence"] = score(row)
    return row


def clean(raw: dict[str, Any], text: str, date_format: str) -> dict[str, Any] | None:
    """One document as the model returned it -> a row: the invoice's values, a level and a note
    for each, and its lines. None when it is not an invoice, receipt or payment, or nothing
    was read. The row is finished by `merge`, which every row goes through."""
    kind = raw.get("kind")
    if kind not in KINDS:
        return None
    values = {f: _text(raw.get(f)) for f in FIELDS}
    for f in ("VendorName", "CustomerName", "BillingAddressRecipient", "VendorAddressRecipient"):
        values[f] = re.sub(r"^(m/s|messrs)\b\.?\s*", "", values[f], flags=re.IGNORECASE)  # a title, not part of the name
    if not any(values[f] for f in ("InvoiceId", "InvoiceDate", "InvoiceTotal", "VendorName")):
        return None
    unsure = {f for f in raw.get("unsure") or [] if f in FIELDS}
    levels = {f: "read" if values[f] else "empty" for f in FIELDS}
    notes: dict[str, str] = {}
    has_text = bool(text.strip())

    def flag(field: str, note: str) -> None:
        levels[field] = "check"
        notes[field] = note

    # Names, numbers and addresses: is the value in the file's own text?
    for f in ("InvoiceId", "CustomerId", "VendorName", "CustomerName", "VendorAddress", "BillingAddress"):
        if values[f] and has_text:
            if _in_text(f, values[f], text):
                levels[f] = "ok"
            elif f in ("InvoiceId", "CustomerId"):
                flag(f, "Not found in the file's text.")
            # A name can be in a logo and an address reworded: not finding those says little.

    # The recipients are the names printed with the addresses: by default the party's own name.
    for recipient, name, address in (
        ("BillingAddressRecipient", "CustomerName", "BillingAddress"), ("VendorAddressRecipient", "VendorName", "VendorAddress"),
    ):
        if not values[recipient] and values[name] and values[address]:
            values[recipient] = values[name]
        if values[recipient]:
            same = _plain(values[recipient]) == _plain(values[name])
            levels[recipient] = levels[name] if same else "ok" if has_text and _in_text(recipient, values[recipient], text) else "read"

    for f in ("VendorGST", "CustomerGST"):
        values[f], levels[f], note = read_gstin(values[f], text)
        if note:
            notes[f] = note
    if values["VendorGST"] and values["VendorGST"] == values["CustomerGST"]:
        for f in ("VendorGST", "CustomerGST"):
            flag(f, "Seller and buyer have the same GSTIN here: one of them is in the wrong place.")
    if values["VendorName"] and _plain(values["VendorName"]) == _plain(values["CustomerName"]):
        for f in ("VendorName", "CustomerName"):
            flag(f, "Seller and buyer are the same name here: one of them is in the wrong place.")

    dates: dict[str, date] = {}
    for f in ("InvoiceDate", "DueDate"):
        if not values[f]:
            continue
        written, parsed = values[f], parse_date(values[f])
        if parsed is None and f == "DueDate" and re.search(r"\bdays?\b|\bnet\b|immediate|receipt", written, re.IGNORECASE):
            values[f], levels[f], notes[f] = "", "empty", f"The page gives payment terms ({written}), not a due date."
            continue
        if parsed is None:
            flag(f, "Could not be read as a date; left as written.")
            continue
        dates[f] = parsed
        values[f] = parsed.strftime(date_format)
        if not date.today().year - OLD_YEARS <= parsed.year <= date.today().year + 1:
            flag(f, f"The year {parsed.year} is unlikely for this date (written: {written}).")
        elif has_text and _plain(written) in _plain(text):
            levels[f] = "ok"
    if len(dates) == 2 and dates["DueDate"] < dates["InvoiceDate"]:
        flag("DueDate", "The due date is before the invoice date.")

    in_words = words_amount(_text(raw.get("TotalInWords")))
    if values["InvoiceTotal"]:
        amount = parse_amount(values["InvoiceTotal"])
        if amount is None:
            flag("InvoiceTotal", "Could not be read as an amount; left as written.")
        else:
            values["InvoiceTotal"] = f"{amount:.2f}"
            if in_words is not None and abs(in_words - amount) < 1:  # words are often rounded to the rupee
                levels["InvoiceTotal"] = "ok"
                notes["InvoiceTotal"] = "Matches the amount in words."
            elif in_words is not None:
                flag("InvoiceTotal", f"The amount in words on the page says {in_words:,.2f}.")
            elif has_text:
                if _amount_in_text(amount, text):
                    levels["InvoiceTotal"] = "ok"
                else:
                    flag("InvoiceTotal", "This amount is not in the file's text.")
    elif in_words is not None:
        values["InvoiceTotal"] = f"{in_words:.2f}"
        flag("InvoiceTotal", "Taken from the amount in words; no figure was read.")

    # ── What was bought: one entry for each line ──
    items = read_items(raw, text if has_text else "")

    # ── The tax breakdown ──
    total = parse_amount(values["InvoiceTotal"]) if values["InvoiceTotal"] else None
    money: dict[str, float] = {}
    for f in ("Discount", "TaxableValue", *TAXES):
        if not values[f]:
            continue
        amount = parse_money(values[f])
        if amount is None and "%" in values[f]:
            values[f], levels[f], notes[f] = "", "empty", "Only the rate was read, not the amount."
        elif amount is None:
            flag(f, "Could not be read as an amount; left as written.")
        elif amount == 0 and f != "TaxableValue":
            values[f], levels[f] = "", "empty"  # printed as 0.00: not charged
        else:
            money[f] = amount
            values[f] = f"{amount:.2f}"
            if has_text:
                if _amount_in_text(amount, text):
                    levels[f] = "ok"
                else:
                    flag(f, "This amount is not in the file's text.")
    for f in TAXES:  # seen with a real model: the invoice total entered as a tax
        if f in money and total and abs(money[f] - total) < 0.01:
            del money[f]
            values[f], levels[f], notes[f] = "", "empty", "The model gave the invoice total here. That is not a tax, so it was left out."
    confirmed = {f for f in money if levels[f] == "ok"}  # by the file's own text

    # TotalTaxAmount is the sum of the three, so the sheet can be checked at a glance. A page
    # that gives one tax figure with no split (a restaurant bill) keeps that figure.
    charged = [f for f in TAXES if f in money]
    printed = parse_money(_text(raw.get("TotalTax")))
    tax: float | None = None
    if charged:
        tax = round(sum(money[f] for f in charged), 2)
        levels["TotalTaxAmount"] = "ok" if all(levels[f] == "ok" for f in charged) else "read"
    elif printed:
        tax = printed
        levels["TotalTaxAmount"] = "ok" if has_text and _amount_in_text(tax, text) else "read"
        notes["TotalTaxAmount"] = "The page gives one tax figure, not split into CGST, SGST and IGST."
    if tax is not None:
        values["TotalTaxAmount"] = f"{tax:.2f}"

    # Does it add up? Taxable value + tax = total holds on every invoice, whoever made it, and
    # needs no text to compare with: it is the one check that also works on a scan or a photo.
    if "TaxableValue" in money and total is not None:
        base, discount = money["TaxableValue"], money.get("Discount", 0.0)

        def fits(added: float) -> str:
            if abs(base + added - total) <= ROUND_OFF:
                return "plain"
            return "less" if discount and abs(base - discount + added - total) <= ROUND_OFF else ""

        how, moved = fits(tax or 0.0), False
        states = {values[f][:2] for f in ("VendorGST", "CustomerGST") if levels[f] == "ok"}
        if (
            not how and charged == ["CGSTAmount", "SGSTAmount"] and len(states) != 1
            and abs(money["CGSTAmount"] - money["SGSTAmount"]) < 0.01 and fits(money["CGSTAmount"])
        ):
            # Seen with a real model: one tax amount entered as both CGST and SGST. Only one
            # such amount fits the total, so it is a single tax - IGST - unless seller and buyer
            # are known to be in the same state, where IGST cannot be.
            tax, how, moved = money["CGSTAmount"], fits(money["CGSTAmount"]), True
            for f in ("CGSTAmount", "SGSTAmount"):
                del money[f]
                values[f], levels[f] = "", "empty"
                notes.pop(f, None)
            money["IGSTAmount"], charged = tax, ["IGSTAmount"]
            values["IGSTAmount"] = values["TotalTaxAmount"] = f"{tax:.2f}"
        added = tax or 0.0
        if how:
            proven = ["TaxableValue", *charged, *(["TotalTaxAmount"] if tax is not None else []), *(["Discount"] if how == "less" else [])]
            for f in proven:
                levels[f] = "ok"
                notes.pop(f, None)
            notes["TaxableValue"] = "Taxable value" + ("" if how == "plain" else " less the discount") + (" + tax" if added else "") + " adds up to the total."
            if levels["InvoiceTotal"] == "read":
                levels["InvoiceTotal"], notes["InvoiceTotal"] = "ok", "Taxable value + tax adds up to it."
            if moved:
                levels["IGSTAmount"] = "read"
                notes["IGSTAmount"] = (
                    "The model gave this amount as both CGST and SGST, but only one such amount fits the total: "
                    "it is a single tax, entered here as IGST. Compare with the invoice."
                )
        else:
            # Which figure is wrong? Not one the file's own text confirms. If the text confirms
            # them all, the invoice has a charge that is neither: the taxable value carries the note.
            doubted = [f for f in ("TaxableValue", *charged) if levels[f] != "ok"] or ["TaxableValue"]
            said = (
                f"The total is {total:,.2f} and the taxable value {base:,.2f}, and no tax amount was read: a tax or another charge is missing here."
                if tax is None else
                f"Taxable value {base:,.2f} + tax {added:,.2f} = {base + added:,.2f}, but the total is {total:,.2f}: "
                "another charge on the invoice, or a misread figure."
            )
            for f in doubted:
                flag(f, said)
    if charged and printed and abs(printed - tax) > ROUND_OFF:
        flag("TotalTaxAmount", f"CGST + SGST + IGST = {tax:,.2f}, but the page's own tax total says {printed:,.2f}.")
    if "CGSTAmount" in money and "SGSTAmount" in money:
        if abs(money["CGSTAmount"] - money["SGSTAmount"]) > ROUND_OFF:
            pair = ("CGSTAmount", "SGSTAmount")
            for f in [f for f in pair if f not in confirmed] or pair:
                flag(f, f"CGST ({money['CGSTAmount']:,.2f}) and SGST ({money['SGSTAmount']:,.2f}) are normally the same amount.")
    elif "CGSTAmount" in money or "SGSTAmount" in money:
        notes["SGSTAmount" if "CGSTAmount" in money else "CGSTAmount"] = "CGST and SGST are charged together; this one was not read."
    if "IGSTAmount" in money and len(charged) > 1:
        for f in charged:
            flag(f, "An invoice charges either IGST, or CGST and SGST, not both: one of these is misread.")

    # The currency is in what the model copied (a sign, a code, "Rupees ... Only"), or failing
    # that in the page's text when it names just one.
    values["Currency"] = currency_of(*(str(raw.get(f) or "") for f in ("InvoiceTotal", "TotalInWords", "TaxableValue", "TotalTax")), text)
    levels["Currency"] = "read" if values["Currency"] else "empty"

    for f in unsure:
        if levels[f] == "read":
            flag(f, "The model could not read this clearly.")
    for f in EXPECTED[kind]:
        if not values[f]:
            notes[f] = "Not found on the page."

    row = {"kind": kind, "values": values, "levels": levels, "notes": notes, "items": items}
    if "Items" in (raw.get("unsure") or []):
        row["items_unsure"] = True
    return row  # `merge` finishes it, once the pages of one invoice are together


def score(row: dict[str, Any]) -> float:
    total = weight = 0.0
    for f in WEIGHT:
        level = row["levels"][f]
        if level == "empty":
            if f not in EXPECTED[row["kind"]]:
                continue  # a due date that is not on the invoice says nothing about the reading
            points = MISSING
        else:
            points = SCORE[level]
        total += points * WEIGHT[f]
        weight += WEIGHT[f]
    return round(total / weight, 2) if weight else 0.0


# ── Pages into invoices ──────────────────────────────────────────────────────

def _key(value: str) -> str:
    return re.sub(r"[^0-9a-z]", "", value.lower())


def _continues(first: dict[str, Any], second: dict[str, Any]) -> str:
    """"same" when two rows from following pages are one invoice, "rest" when the second is
    only the tail of the first (totals, no heading), "" when they are separate documents."""
    if second["pages"][0] <= first["pages"][-1]:
        return ""  # both on one page: two documents
    a, b = first["values"], second["values"]
    if _key(a["InvoiceId"]) and _key(a["InvoiceId"]) == _key(b["InvoiceId"]):
        return "same"
    if not any(b[f] for f in ("InvoiceId", "VendorName", "CustomerName", "InvoiceDate")):
        return "rest"
    return ""


def merge(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows in page order -> one finished row per invoice. An invoice running over several
    pages repeats its number on each; its lines go on from page to page and its grand total
    is on the last."""
    merged: list[dict[str, Any]] = []
    for row in rows:
        how = _continues(merged[-1], row) if merged else ""
        if not how:
            merged.append(row)
            continue
        kept = merged[-1]
        for f in FIELDS:
            new, old = row["values"][f], kept["values"][f]
            if not new:
                continue
            later = f in AMOUNTS  # the totals and the tax are on the last page
            better = SCORE.get(row["levels"][f], 0) > SCORE.get(kept["levels"][f], 0)
            if not old or later or (how == "same" and better):
                kept["values"][f], kept["levels"][f] = new, row["levels"][f]
                kept["notes"].pop(f, None)
                if f in row["notes"]:
                    kept["notes"][f] = row["notes"][f]
        more = [item for item in row["items"] if _is_item(item)]
        if more:  # the lines go on over the page
            kept["items"] = [item for item in kept["items"] if _is_item(item)] + more
        if row.get("items_unsure"):
            kept["items_unsure"] = True
        kept["pages"] += row["pages"]
    return [finish(row) for row in merged]
