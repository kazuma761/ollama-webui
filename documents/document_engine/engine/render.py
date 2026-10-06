"""Fills a prepared template from its slot map: same design, new content.

The output is the template's own XML with text replaced, repeated blocks
copied where the content has more items than the sample, and removed where
it has fewer. Nothing is laid out afresh, so columns, colours, pictures,
fonts and shapes are whatever the template has.

Limits that follow from that: a text box keeps its size, so much longer text
can overflow it (reported as a warning, never "fixed" by resizing), and a
`boxes` group (skill chips) can drop items but cannot grow new ones.
"""

from __future__ import annotations

import copy
import secrets
from typing import Any

from .slotmap import item_shape
from .wordfile import MC_ALTERNATE, W_P, W_R, WordFile, box_paragraphs, clean_copy, paragraph_text, write_paragraph

_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_VML_ID_TAGS = ("shape", "rect", "roundrect", "oval", "line", "shapetype", "group")
OVERFLOW = 1.5  # a text box filled with more than this many times its sample text gets a warning


def _siblings(first: Any, last: Any) -> list[Any]:
    """Everything from one element to a later sibling, both included."""
    run, node = [], first
    while node is not None:
        run.append(node)
        if node is last:
            return run
        node = node.getnext()
    return [first]


class Renderer:
    def __init__(self, word: WordFile, slotmap: dict[str, Any]):
        self.word, self.slotmap = word, slotmap
        self.warnings: list[str] = []
        roots = [word.doc.element.body, *word._parts]
        self._drawing_id = max(
            (int(e.get("id")) for root in roots for e in root.iter(_WP + "docPr") if (e.get("id") or "").isdigit()), default=0
        )
        self._copies = 0

    # ── Locating a labelled paragraph inside a copy of its block ──

    def _path(self, core: list[Any], block_id: str) -> tuple[int, int | None]:
        """Where a labelled paragraph sits in its block: (which paragraph, which text-box paragraph on it)."""
        block = self.word.block(block_id)
        if block.where != "textbox":
            return next(n for n, node in enumerate(core) if node is block.element), None
        anchor = self.word.block(block.anchor).element
        boxes = box_paragraphs(anchor)
        return next(n for n, node in enumerate(core) if node is anchor), next(n for n, p in enumerate(boxes) if p is block.element)

    @staticmethod
    def _at(core: list[Any], path: tuple[int, int | None]) -> Any:
        node = core[path[0]]
        return node if path[1] is None else box_paragraphs(node)[path[1]]

    def _fresh(self, node: Any) -> Any:
        """A copy of an element that can live in the same file: no duplicate drawing or shape ids."""
        clone = clean_copy(node) if node.tag == W_P else copy.deepcopy(node)
        self._copies += 1
        for element in clone.iter():
            if not isinstance(element.tag, str):
                continue
            if element.tag == _WP + "docPr":
                self._drawing_id += 1
                element.set("id", str(self._drawing_id))
            elif element.tag.startswith("{urn:schemas-microsoft-com:vml}") and element.tag.split("}")[1] in _VML_ID_TAGS and element.get("id"):
                element.set("id", f"{element.get('id')}_{self._copies}")
            for attribute in list(element.attrib):
                local = attribute.split("}")[-1]
                if local in ("paraId", "textId"):
                    del element.attrib[attribute]
                elif local in ("anchorId", "editId"):
                    element.set(attribute, secrets.token_hex(4).upper())
                elif local == "spid":
                    element.set(attribute, f"{element.get(attribute)}_{self._copies}")
        return clone

    # ── Writing ──

    def _write(self, paragraph: Any, value: Any, label: str, sample: str | None = None) -> None:
        text = str(value).strip() if value is not None else ""
        sample = paragraph_text(paragraph).strip() if sample is None else sample
        in_box = any(a.tag.endswith("}txbxContent") for a in paragraph.iterancestors())
        if in_box and sample and len(text) > OVERFLOW * max(len(sample), 12):
            self.warnings.append(f"\"{label}\" is much longer than the template's sample and may not fit its box")
        if not text:
            self.warnings.append(f"Nothing for \"{label}\": the template's sample text there was removed")
        write_paragraph(paragraph, text)

    def _write_list(self, paragraphs: list[Any], lines: list[Any], label: str) -> None:
        lines = [str(line).strip() for line in (lines or []) if str(line).strip()]
        if not paragraphs:
            return
        pattern = clean_copy(paragraphs[0])
        while len(paragraphs) < len(lines):
            extra = copy.deepcopy(pattern)
            paragraphs[-1].addnext(extra)
            paragraphs.append(extra)
        for paragraph in paragraphs[max(len(lines), 1):]:
            paragraph.getparent().remove(paragraph)
        if not lines:
            self.warnings.append(f"Nothing for \"{label}\": the template's sample text there was removed")
            write_paragraph(paragraphs[0], "")
        for paragraph, line in zip(paragraphs, lines):
            write_paragraph(paragraph, line)

    def _fill_item(self, core: list[Any], instance: dict[str, Any], paths: dict[str, Any], item: Any, label: str) -> None:
        names, lists = list(instance["fields"]), list(instance["lists"])
        if not isinstance(item, dict):
            item = {names[0]: item} if names else {}
        for name in names:
            self._write(self._at(core, paths["fields"][name]), item.get(name), f"{label} {name}".replace("_", " "))
        for name in lists:
            targets = [self._at(core, path) for path in paths["lists"][name]]
            self._write_list(targets, item.get(name) if isinstance(item.get(name), list) else [], f"{label} {name}".replace("_", " "))

    def _range_group(self, group: dict[str, Any], items: list[Any]) -> None:
        word, instances = self.word, group["instances"]
        cores = [_siblings(word.block(i["start"]).element, word.block(i["end"]).element) for i in instances]
        paths = [
            {
                "fields": {name: self._path(core, block_id) for name, block_id in inst["fields"].items()},
                "lists": {name: [self._path(core, b) for b in ids] for name, ids in inst["lists"].items()},
            }
            for inst, core in zip(instances, cores)
        ]
        # What separates one item from the next in the template (usually empty lines).
        gap: list[Any] = []
        node = cores[0][-1].getnext()
        if len(cores) > 1:
            while node is not None and node is not cores[1][0]:
                gap.append(node)
                node = node.getnext()
        elif node is not None and node.tag == W_P and not paragraph_text(node).strip() and not box_paragraphs(node):
            gap.append(node)  # a single sample item: the empty line after it serves as the spacing

        filled: list[tuple[list[Any], dict[str, Any], dict[str, Any]]] = []
        for number in range(len(items)):
            if number < len(cores):
                filled.append((cores[number], instances[number], paths[number]))
                continue
            # More items than the sample has: copy the first item's block after the last one.
            after = filled[-1][0][-1]
            for node in [self._fresh(g) for g in gap] + (new := [self._fresh(n) for n in cores[0]]):
                after.addnext(node)
                after = node
            filled.append((new, instances[0], paths[0]))

        for number in range(len(items), len(cores)):  # fewer items: remove the sample's extra blocks
            before = cores[number][0].getprevious()
            for node in cores[number]:
                node.getparent().remove(node)
            # and the spacing that led up to it, when the previous item is still there
            if number > 0:
                for _ in gap:
                    if before is not None and before.tag == W_P and not paragraph_text(before).strip() and not box_paragraphs(before):
                        previous = before.getprevious()
                        before.getparent().remove(before)
                        before = previous
        if not items:
            self.warnings.append(f"No {group['name'].replace('_', ' ')} found: that part of the template was removed")

        for number, (core, instance, path) in enumerate(filled, 1):
            self._fill_item(core, instance, path, items[number - 1], f"{group['name']} {number}")

    def _boxes_group(self, group: dict[str, Any], items: list[Any]) -> None:
        instances = group["instances"]
        name = group["name"].replace("_", " ")
        values = [next(iter(i.values()), "") if isinstance(i, dict) else i for i in items]
        values = [str(v).strip() for v in values if str(v).strip()]
        if not values:
            self.warnings.append(f"No {name} found: those boxes were removed")
        if len(values) > len(instances):
            self.warnings.append(f"The template has room for {len(instances)} {name}; {len(values) - len(instances)} more were left out")
        for number, instance in enumerate(instances):
            paragraph = self.word.block(next(iter(instance["fields"].values()))).element
            if number < len(values):
                self._write(paragraph, values[number], f"{name} {number + 1}")
                continue
            # No value for this box: remove the whole shape, not just its text.
            shape = next((a for a in paragraph.iterancestors(MC_ALTERNATE)), None)
            run = next((a for a in shape.iterancestors(W_R)), None) if shape is not None else None
            if run is not None and run.getparent() is not None:
                run.getparent().remove(run)
            else:
                write_paragraph(paragraph, "")

    def render(self, values: dict[str, Any]) -> dict[str, Any]:
        for field in self.slotmap["fields"]:
            self._write(self.word.block(field["id"]).element, values.get(field["name"]), field["name"].replace("_", " "))
        for group in self.slotmap["groups"]:
            items = values.get(group["name"])
            items = items if isinstance(items, list) else []
            names, lists = item_shape(group)
            items = [i for i in items if i not in ("", None, {})]
            if group["kind"] == "boxes":
                self._boxes_group(group, items)
            else:
                self._range_group(group, items)
        return {"warnings": self.warnings}


def render(name: str, data: bytes, slotmap: dict[str, Any], values: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    word = WordFile(name, data)
    report = Renderer(word, slotmap).render(values)
    result = word.save()
    return result, {**report, "preview": WordFile(name, result).preview()}
