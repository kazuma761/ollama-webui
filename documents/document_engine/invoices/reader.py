"""Turns an invoice file into pages a model can read. No model is used here.

Every page comes out as text, a picture, or both:

  PDF with real text   the text, laid out as on the page, and a picture of the page
  scanned PDF, photo   a picture only
  Word file            its text (body, tables, text boxes, header and footer),
                       and each large picture inside it as a page of its own

The picture matters even when there is text: on a page it shows which block is
the seller and which the buyer, where the text alone can come out jumbled.
"""

from __future__ import annotations

import io
import re
import threading
import unicodedata
import zipfile
from dataclasses import dataclass, field

from ollama_pipeline import UnsupportedDocument

IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")
ACCEPTED = (".pdf", ".docx", *IMAGE_TYPES)

# A PDF page with less text than this is a scan or a photo. What little text it has is page
# furniture - a print date, a file name, a link - and reading it as the invoice gives wrong
# answers, so it is dropped and only the picture is used.
MIN_TEXT = 200
MAX_DPI = 300
# Pictures inside a Word file smaller or narrower than this are logos, stamps and signatures.
MIN_PICTURE_SIDE = 500
MAX_PICTURE_SHAPE = 3.2

_pdfium = threading.Lock()  # PDFium must not be used from two threads at once


@dataclass
class Page:
    number: int
    text: str = ""
    image: bytes | None = None  # JPEG
    kind: str = ""  # "text", "scan", "photo", "word", "word picture" - how the page was read


@dataclass
class FileRead:
    name: str
    pages: list[Page] = field(default_factory=list)
    total: int = 0  # pages in the file, which can be more than were read
    notes: list[str] = field(default_factory=list)


def _extension(name: str) -> str:
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _scale(width: float, height: float, side: int, most: float) -> float:
    """How much to scale a page or photo so a model can read small print without the
    picture getting huge. `side` is roughly the longer side of an A4 page, in pixels; a long
    narrow receipt may be up to half as long again."""
    area = side * side * 0.72
    return min(most, (area / (width * height)) ** 0.5, side * 1.5 / max(width, height))


def _jpeg(image, side: int) -> bytes:
    from PIL import Image, ImageOps

    image = ImageOps.exif_transpose(image)  # a phone photo stores its rotation separately
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        white = Image.new("RGB", image.size, "white")
        white.paste(image, mask=image.getchannel("A"))
        image = white
    image = image.convert("RGB")
    scale = _scale(image.width, image.height, side, 1.0)
    if scale < 1.0:
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=88)
    return out.getvalue()


def _tidy(text: str) -> str:
    """Layout text has long runs of spaces and blank lines; keep the shape, drop the bulk."""
    text = unicodedata.normalize("NFKC", text.replace("\x00", ""))  # "ﬀ" as "ff", odd spaces as spaces
    lines = [re.sub(r" {7,}", "      ", line.rstrip()) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")


def _pdf_text(data: bytes, count: int) -> list[str]:
    """The text of each page, laid out as on the page where pypdf can manage it."""
    from pypdf import PdfReader

    texts = []
    try:
        pages = PdfReader(io.BytesIO(data)).pages
    except Exception:
        return [""] * count
    for index in range(count):
        text = ""
        for mode in ("layout", "plain"):
            try:
                text = pages[index].extract_text(extraction_mode=mode) or ""
                break
            except Exception:
                continue
        texts.append(_tidy(text))
    return texts


def _pdf(name: str, data: bytes, max_pages: int, side: int) -> FileRead:
    import pypdfium2 as pdfium

    read = FileRead(name)
    with _pdfium:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            if "password" in str(exc).lower():
                raise UnsupportedDocument(f"{name} is password-protected. Remove the password and add it again.") from exc
            raise UnsupportedDocument(f"{name} could not be opened as a PDF.") from exc
        try:
            read.total = len(pdf)
            count = min(read.total, max_pages)
            pictures = []
            for index in range(count):
                page = pdf[index]
                width, height = page.get_size()
                bitmap = page.render(scale=_scale(width, height, side, MAX_DPI / 72))
                pictures.append(_jpeg(bitmap.to_pil(), side * 2))
        finally:
            pdf.close()
    for index, text in enumerate(_pdf_text(data, count)):
        real = len(re.sub(r"\s+", "", text)) >= MIN_TEXT
        read.pages.append(Page(index + 1, text if real else "", pictures[index], "text" if real else "scan"))
    if read.total > count:
        read.notes.append(f"Only the first {count} of {read.total} pages were read.")
    return read


def _photo(name: str, data: bytes, max_pages: int, side: int) -> FileRead:
    from PIL import Image, ImageSequence

    read = FileRead(name)
    try:
        image = Image.open(io.BytesIO(data))
        frames = [frame.copy() for frame in ImageSequence.Iterator(image)]  # a TIFF can hold several pages
    except Exception as exc:
        raise UnsupportedDocument(f"{name} could not be opened as a picture.") from exc
    read.total = len(frames)
    for index, frame in enumerate(frames[:max_pages]):
        read.pages.append(Page(index + 1, "", _jpeg(frame, side), "photo"))
    if read.total > max_pages:
        read.notes.append(f"Only the first {max_pages} of {read.total} pages were read.")
    return read


def _word_text(document) -> str:
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    def table_lines(table) -> list[str]:
        lines = []
        for row in table.rows:
            cells: list[str] = []
            for cell in row.cells:  # a merged cell is listed once per column it spans
                text = " ".join(cell.text.split())
                if not cells or cells[-1] != text:
                    cells.append(text)
            if any(cells):
                lines.append(" | ".join(cells))
        return lines

    def part_lines(part) -> list[str]:
        lines = []
        for child in part._element.iterchildren():
            if child.tag == qn("w:p"):
                text = Paragraph(child, part).text.strip()
                if text:
                    lines.append(text)
            elif child.tag == qn("w:tbl"):
                lines += table_lines(Table(child, part))
        return lines

    lines = part_lines(document)
    # Text boxes: a letterhead often has the company's name and address in one. Word stores
    # each box twice, the second copy under mc:Fallback, which is skipped.
    fallback = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
    boxes = []
    for box in document.element.body.iter(qn("w:txbxContent")):
        if any(parent.tag == fallback for parent in box.iterancestors()):
            continue
        text = "\n".join(t for p in box.iter(qn("w:p")) if (t := "".join(n.text or "" for n in p.iter(qn("w:t"))).strip()))
        if text and text not in boxes:
            boxes.append(text)
    if boxes:
        lines += ["", "[Text boxes on the page]", *boxes]
    for title, attribute in (("Page header", "header"), ("Page footer", "footer")):
        seen: list[str] = []
        for section in document.sections:
            try:
                found = part_lines(getattr(section, attribute))
            except Exception:
                found = []
            seen += [line for line in found if line not in seen]
        if seen:
            lines += ["", f"[{title}]", *seen]
    return "\n".join(lines).strip()


def _word(name: str, data: bytes, max_pages: int, side: int) -> FileRead:
    import docx
    from PIL import Image

    read = FileRead(name)
    try:
        text = _word_text(docx.Document(io.BytesIO(data)))
    except Exception as exc:
        raise UnsupportedDocument(f"{name} could not be opened as a Word file. Old .doc files need saving as .docx or PDF first.") from exc
    if len(re.sub(r"\s+", "", text)) >= 40:
        read.pages.append(Page(1, text, None, "word"))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in sorted(archive.namelist()):
            if not entry.startswith("word/media/") or _extension(entry) not in (*IMAGE_TYPES, ".gif"):
                continue
            try:
                image = Image.open(io.BytesIO(archive.read(entry)))
                image.load()
            except Exception:
                continue
            small, large = sorted(image.size)
            if small < MIN_PICTURE_SIDE or large / small > MAX_PICTURE_SHAPE:
                continue
            if len(read.pages) >= max_pages:
                read.notes.append(f"Only the first {max_pages} pictures in the file were read.")
                break
            read.pages.append(Page(len(read.pages) + 1, "", _jpeg(image, side), "word picture"))
    read.total = len(read.pages)
    if not read.pages:
        raise UnsupportedDocument(f"{name} has no text and no picture of an invoice in it.")
    return read


def read_file(name: str, data: bytes, max_pages: int = 40, side: int = 1600) -> FileRead:
    """A file as pages. Raises UnsupportedDocument with a message for the user."""
    kind = _extension(name)
    if data[:5] == b"%PDF-":
        kind = ".pdf"
    elif data[:4] == b"PK\x03\x04" and kind != ".docx":
        raise UnsupportedDocument(f"{name}: of the Office formats only Word .docx is read. Save it as .docx or PDF.")
    elif data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise UnsupportedDocument(f"{name} is an old Office file (.doc or .xls). Save it as .docx or PDF and add it again.")
    if kind == ".pdf":
        return _pdf(name, data, max_pages, side)
    if kind == ".docx":
        return _word(name, data, max_pages, side)
    if kind in IMAGE_TYPES:
        return _photo(name, data, max_pages, side)
    raise UnsupportedDocument(f"{name}: invoices are read from PDF, Word (.docx) and pictures ({', '.join(t[1:] for t in IMAGE_TYPES)}).")
