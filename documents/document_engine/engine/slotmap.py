"""The slot map of a prepared template: what each of its paragraphs is for.

A designed template (a resume, a report with a fixed look) has no blanks. It
is full of sample text. Preparing it means labelling every paragraph once:

- `fixed`       a label or boilerplate that never changes ("EDUCATION")
- `field`       a single value ("name", "email", "summary")
- `group_item`  part of a block that repeats ("jobs" number 2: its "title",
                its "company_dates", each of its "bullets")
- `empty`       spacing

A model proposes the labels and a person reviews them; `build` turns the
reviewed labels into the slot map stored next to the template. Nothing in
here talks to a model or changes a file.

Groups come in two kinds. A `range` group is a run of paragraphs that can be
copied to make more items and removed to make fewer. A `boxes` group is a set
of text boxes sharing a paragraph (skill chips): each can be filled or
removed, but a new one cannot be added, because its place on the page is fixed.
"""

from __future__ import annotations

import re
from typing import Any

from .wordfile import W_P, WordFile, paragraph_text

ROLES = ("fixed", "field", "group_item", "empty")


def snake(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")[:40]


def guess(word: WordFile) -> dict[str, dict[str, Any]]:
    """Labels that need no model: spacing, and lines that are plainly section headings."""
    labels: dict[str, dict[str, Any]] = {}
    written = [b for b in word.blocks if b.where in ("body", "textbox") and paragraph_text(b.element).strip()]
    for position, block in enumerate(written):
        text = " ".join(paragraph_text(block.element).split())
        letters = [c for c in text if c.isalpha()]
        shouting = bool(letters) and all(c.isupper() for c in letters) and len(text) <= 40
        heading = word.heading_level(block.element) is not None
        # The first line of a document in capitals is far more often a name or a title than a heading.
        if (shouting and position > 0 or heading) and position + 1 < len(written):
            labels[block.id] = {"role": "fixed"}
    return labels


def build(word: WordFile, labels: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    """Turns reviewed labels into a slot map. Returns it with a list of what was dropped and why."""
    order = {block.id: number for number, block in enumerate(word.blocks)}
    problems: list[str] = []
    fields: list[dict[str, Any]] = []
    fixed: list[str] = []
    grouped: dict[str, dict[int, list[tuple[str, str]]]] = {}
    used: set[str] = set()

    for block_id, label in labels.items():
        if block_id not in order:
            problems.append(f"{block_id} is not a paragraph of this template")
            continue
        role, name = label.get("role"), snake(label.get("name"))
        if role == "fixed":
            fixed.append(block_id)
        elif role == "field":
            if not name:
                problems.append(f"{block_id} is marked as a field but has no name")
                continue
            base, number = name, 2
            while name in used:
                name, number = f"{base}_{number}", number + 1
            used.add(name)
            fields.append({"name": name, "id": block_id, "sample": paragraph_text(word.block(block_id).element).strip()})
        elif role == "group_item":
            group = snake(label.get("group"))
            try:
                instance = int(label.get("instance") or 1)
            except (TypeError, ValueError):
                instance = 1
            if not group or not name:
                problems.append(f"{block_id} is part of a group but its group or name is missing")
                continue
            grouped.setdefault(group, {}).setdefault(instance, []).append((block_id, name))

    def top(block_id: str) -> str:
        """The body paragraph a label sits on: itself, or the one its text box hangs on."""
        return word.block(block_id).anchor or block_id

    groups: list[dict[str, Any]] = []
    for group, numbered in grouped.items():
        repeated = {
            name for items in numbered.values()
            for name in {n for _, n in items} if sum(1 for _, other in items if other == name) > 1
        }
        instances = []
        for number in sorted(numbered):
            items = sorted(numbered[number], key=lambda item: order[item[0]])
            single: dict[str, str] = {}
            lists: dict[str, list[str]] = {}
            for block_id, name in items:
                if name in repeated:
                    lists.setdefault(name, []).append(block_id)
                else:
                    single[name] = block_id
            tops = sorted({top(block_id) for block_id, _ in items}, key=lambda i: order[i])
            instances.append({"fields": single, "lists": lists, "start": tops[0], "end": tops[-1]})
        instances.sort(key=lambda instance: order[instance["start"]])

        every = [block_id for inst in instances for block_id in [*inst["fields"].values(), *sum(inst["lists"].values(), [])]]
        chips = all(len(inst["fields"]) == 1 and not inst["lists"] for inst in instances) and all(
            word.block(block_id).where == "textbox" for block_id in every
        )
        shared = len({top(block_id) for block_id in every}) < len(instances) or any(
            paragraph_text(word.block(top(block_id)).element).strip() for block_id in every
        )
        if chips and shared:
            groups.append({"name": group, "kind": "boxes", "instances": instances})
            continue

        parents = {id(word.block(inst[edge]).element.getparent()) for inst in instances for edge in ("start", "end")}
        overlap = any(order[b["start"]] <= order[a["end"]] for a, b in zip(instances, instances[1:]))
        if len(parents) != 1 or overlap:
            problems.append(f"The items of '{group}' are not separate runs of paragraphs, so the group was left out")
            continue
        groups.append({"name": group, "kind": "range", "instances": instances})

    return {"version": 1, "fields": fields, "groups": groups, "fixed": fixed}, problems


def item_shape(group: dict[str, Any]) -> tuple[list[str], list[str]]:
    """The field names and list names of one item, taken from the first instance."""
    first = group["instances"][0]
    return list(first["fields"]), list(first["lists"])


def values_schema(slotmap: dict[str, Any], only: str | None = None) -> dict[str, Any]:
    """The JSON shape a model must answer in. `only` limits it to the single fields ("") or one group."""
    properties: dict[str, Any] = {}
    if only in (None, ""):
        for field in slotmap["fields"]:
            properties[field["name"]] = {"type": "string"}
    for group in slotmap["groups"]:
        if only not in (None, group["name"]):
            continue
        names, lists = item_shape(group)
        if len(names) == 1 and not lists:
            item: dict[str, Any] = {"type": "string"}
        else:
            item = {
                "type": "object",
                "properties": {**{n: {"type": "string"} for n in names}, **{n: {"type": "array", "items": {"type": "string"}} for n in lists}},
                "required": [*names, *lists],
            }
        properties[group["name"]] = {"type": "array", "items": item}
    return {"type": "object", "properties": properties, "required": list(properties)}


def describe(word: WordFile, slotmap: dict[str, Any], only: str | None = None) -> str:
    """The slots as text for a model, each with the template's sample so it knows what kind of thing goes there."""
    def sample(block_id: str) -> str:
        text = " ".join(paragraph_text(word.block(block_id).element).split())
        return text if len(text) <= 90 else text[:87] + "..."

    lines = []
    if only in (None, ""):
        for field in slotmap["fields"]:
            lines.append(f'- "{field["name"]}": one value. The template\'s sample: "{sample(field["id"])}"')
    for group in slotmap["groups"]:
        if only not in (None, group["name"]):
            continue
        first = group["instances"][0]
        names, lists = item_shape(group)
        limit = f" At most {len(group['instances'])} items fit." if group["kind"] == "boxes" else ""
        if len(names) == 1 and not lists:
            lines.append(f'- "{group["name"]}": a list of values, one per item.{limit} Sample item: "{sample(first["fields"][names[0]])}"')
            continue
        lines.append(f'- "{group["name"]}": a list of items, one per entry in the source.{limit} Each item has:')
        for name in names:
            lines.append(f'    - "{name}". Sample: "{sample(first["fields"][name])}"')
        for name in lists:
            lines.append(f'    - "{name}": a list of lines. Sample line: "{sample(first["lists"][name][0])}"')
    return "\n".join(lines)


def summary(slotmap: dict[str, Any]) -> dict[str, Any]:
    return {
        "fields": [f["name"] for f in slotmap["fields"]],
        "groups": [
            {"name": g["name"], "kind": g["kind"], "instances": len(g["instances"]), "fields": item_shape(g)[0], "lists": item_shape(g)[1]}
            for g in slotmap["groups"]
        ],
        "fixed": len(slotmap["fixed"]),
    }


def body_paragraphs(word: WordFile, start: str, end: str) -> list[Any]:
    """The run of sibling paragraphs from one body paragraph to another, both included."""
    first, last = word.block(start).element, word.block(end).element
    run, node = [], first
    while node is not None:
        if node.tag == W_P:
            run.append(node)
        if node is last:
            return run
        node = node.getnext()
    return [first]
