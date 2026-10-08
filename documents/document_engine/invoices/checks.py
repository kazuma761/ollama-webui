"""Cleans what the model read off an invoice, checks it, and scores the confidence. No model here.

A model reading a page can misread a character or pick the wrong block, and it
cannot tell you when it has. So nothing it returns is taken on trust: every
value gets one of four levels, and the row's Confidence is worked out from them.

  ok      confirmed by a check: the value is in the file's own text, a GSTIN's
          check digit is right, the total matches the amount in words
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

# The fields the model fills, and the sheet's columns in the order asked for.
FIELDS = (
    "InvoiceId", "InvoiceDate", "DueDate", "InvoiceTotal", "VendorName", "VendorAddress", "CustomerName",
    "CustomerId", "BillingAddress", "BillingAddressRecipient", "VendorAddressRecipient", "VendorGST", "CustomerGST",
)
COLUMNS = (
    ("Invoice File Name", "file"), ("InvoiceId", "InvoiceId"), ("Invoice Date", "InvoiceDate"), ("DueDate", "DueDate"),
    ("InvoiceTotal", "InvoiceTotal"), ("VendorName", "VendorName"), ("VendorAddress", "VendorAddress"),
    ("CustomerName", "CustomerName"), ("CustomerId", "CustomerId"), ("BillingAddress", "BillingAddress"),
    ("BillingAddressRecipient", "BillingAddressRecipient"), ("VendorAddressRecipient", "VendorAddressRecipient"),
    ("VendorGST", "VendorGST"), ("CustomerGST", "CustomerGST"), ("Confidence", "confidence"),
)
KINDS = ("invoice", "receipt", "payment")  # what becomes a row; anything else on a page is skipped

WEIGHT = {
    "InvoiceTotal": 3, "InvoiceId": 2, "InvoiceDate": 2, "VendorName": 2, "CustomerName": 1.5, "VendorGST": 1.5,
    "CustomerGST": 1.5, "VendorAddress": 1, "BillingAddress": 1, "DueDate": 1, "CustomerId": 1,
    "BillingAddressRecipient": 0.5, "VendorAddressRecipient": 0.5,
}
SCORE = {"ok": 1.0, "read": 0.8, "check": 0.35}
MISSING = 0.3
# What a document of each kind should have. A fuel receipt has no buyer and often no number.
EXPECTED = {
    "invoice": ("InvoiceId", "InvoiceDate", "InvoiceTotal", "VendorName", "CustomerName"),
    "receipt": ("InvoiceDate", "InvoiceTotal", "VendorName"),
    "payment": ("InvoiceDate", "InvoiceTotal", "VendorName"),
}
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
    if field in ("VendorAddress", "BillingAddress"):
        words = [w for w in _plain(value).split() if len(w) > 1]
        have = set(_plain(text).split())
        return bool(words) and sum(w in have for w in words) / len(words) >= 0.85
    return bool(_plain(value)) and _plain(value) in _plain(text)


def _amount_in_text(amount: float, text: str) -> bool:
    flat = re.sub(r"(?<=\d),(?=\d)", "", text)
    whole = int(amount)
    forms = {f"{amount:.2f}"} | ({str(whole)} if amount == whole else set())
    return any(re.search(rf"(?<![\d.]){re.escape(form)}(?!\d)", flat) for form in forms)


def clean(raw: dict[str, Any], text: str, date_format: str) -> dict[str, Any] | None:
    """One document as the model returned it -> a row: values for the sheet, a level and a note
    for each. None when it is not an invoice, receipt or payment, or nothing was read."""
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
        if parsed is None:
            flag(f, "Could not be read as a date; left as written.")
            continue
        dates[f] = parsed
        values[f] = parsed.strftime(date_format)
        if not 2015 <= parsed.year <= date.today().year + 1:
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

    for f in unsure:
        if levels[f] == "read":
            flag(f, "The model could not read this clearly.")
    for f in EXPECTED[kind]:
        if not values[f]:
            notes[f] = "Not found on the page."

    row = {"kind": kind, "values": values, "levels": levels, "notes": notes}
    row["confidence"] = score(row)
    return row


def score(row: dict[str, Any]) -> float:
    total = weight = 0.0
    for f in FIELDS:
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
    """Rows in page order -> one row per invoice. An invoice running over several pages
    repeats its number on each, and its grand total is on the last."""
    merged: list[dict[str, Any]] = []
    for row in rows:
        how = _continues(merged[-1], row) if merged else ""
        if not how:
            merged.append(row)
            continue
        kept = merged[-1]
        for f in FIELDS:
            later_total = f == "InvoiceTotal" and row["values"][f]
            better = SCORE.get(row["levels"][f], 0) > SCORE.get(kept["levels"][f], 0)
            if row["values"][f] and (not kept["values"][f] or later_total or (how == "same" and better)):
                kept["values"][f], kept["levels"][f] = row["values"][f], row["levels"][f]
                kept["notes"].pop(f, None)
                if f in row["notes"]:
                    kept["notes"][f] = row["notes"][f]
        kept["pages"] += row["pages"]
        kept["confidence"] = score(kept)
    return merged
