"""A check of the agent skills without a model:  cd backend && python -m agent_skills"""

import asyncio
import tempfile
from pathlib import Path

from .charts import ChartError, ChartTool, build
from .memory import MemoryError_, MemoryStore, MemoryTools
from .settings import DEFAULTS, SkillSettings
from .toolbox import Toolbox, wants_chart, wants_memory


class Other:
    """A stand-in for another tool set, such as the SQL connector's."""

    definitions = [{"type": "function", "function": {"name": "query", "parameters": {}}}]

    def system_prompt(self):
        return "other"

    async def run(self, name, args):
        return "rows", {"rows": 1}


def main() -> None:
    # Charts: what models send, cleaned; what is wrong, refused with the reason.
    chart = build({"type": "bar", "title": "Sales", "labels": ["Pune", "Mysuru"],
                   "series": [{"name": "2026", "values": ["₹1,200.50", 980]}]})
    assert chart["series"][0]["values"] == [1200.5, 980]
    rows = build('{"type": "pie", "title": "Share", "data": [{"city": "Pune", "share": 60}, {"city": "Mysuru", "share": 40}]}')
    assert rows["labels"] == ["Pune", "Mysuru"] and rows["series"][0]["values"] == [60, 40]
    for bad in (
        {"type": "bar", "title": "x", "labels": ["a", "b"], "series": [{"name": "s", "values": [1]}]},
        {"type": "pie", "title": "x", "labels": ["a"], "series": [{"name": "s", "values": [1]}, {"name": "t", "values": [2]}]},
        {"type": "radar", "title": "x", "labels": ["a"], "series": [{"name": "s", "values": [1]}]},
        {"type": "line", "title": "x", "labels": ["a"], "series": [{"name": "s", "values": ["n/a"]}]},
    ):
        try:
            build(bad)
            raise AssertionError(f"not refused: {bad}")
        except ChartError:
            pass
    text, event = asyncio.run(ChartTool().run("create_chart", {"type": "line", "title": "x", "labels": ["a"], "series": [{"name": "s", "values": [1, 2]}]}))
    assert text.startswith("Error:") and event["error"]

    # Gates: only messages about a skill get its tools.
    assert wants_chart("Make a bar chart of sales by city") and wants_chart("plot the trend")
    assert not wants_chart("write an email to my manager")
    assert wants_memory("Remember that the finance head is Priya") and wants_memory("please forget the old address")
    assert not wants_memory("what is the leave policy?")

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        settings = SkillSettings(folder)
        assert settings.get() == DEFAULTS
        assert settings.update({"charts": False, "nonsense": True}) == {**DEFAULTS, "charts": False}
        assert settings.get()["charts"] is False

        store = MemoryStore(folder)
        one = store.add("general", "The finance head is Priya Rao.")
        assert store.add("general", "the finance head is priya rao.")["id"] == one["id"]  # no doubles
        store.add("hr-policies-ab12cd", "Leave requests go to Asha.", by="chat")
        assert [m["text"] for m in store.list(None)] == ["The finance head is Priya Rao."]
        assert "Priya" in store.prompt("general") and store.prompt("finance-12ab34") == ""
        for bad in ("", "x" * 600):
            try:
                store.add("general", bad)
                raise AssertionError("not refused")
            except MemoryError_:
                pass
        try:
            store.add("../etc", "x")
            raise AssertionError("a bad scope was taken")
        except MemoryError_:
            pass
        store.update(one["id"], "The finance head is Priya R. Rao.")
        assert store.list("general")[0]["text"].endswith("R. Rao.")

        tools = MemoryTools(store, "hr-policies-ab12cd")
        text, event = asyncio.run(tools.run("save_memory", {"text": "Payroll closes on the 25th."}))
        assert event["saved"] and len(store.list("hr-policies-ab12cd")) == 2
        text, event = asyncio.run(tools.run("forget_memory", '{"text": "payroll closing date"}'))
        assert event["forgotten"] and "Payroll" in event["memory"], event
        text, event = asyncio.run(tools.run("forget_memory", {"text": "something never saved"}))
        assert not event["forgotten"]
        store.add("hr-policies-ab12cd", "The Pune office opens at 9.")
        store.add("hr-policies-ab12cd", "The Mysuru office opens at 10.")
        text, event = asyncio.run(tools.run("forget_memory", {"text": "office opening time"}))
        assert not event["forgotten"] and "Several" in text and len(store.list("hr-policies-ab12cd")) == 3
        store.drop_scope("hr-policies-ab12cd")
        assert store.list("hr-policies-ab12cd") == [] and store.remove(one["id"]) and store.list("general") == []

        # The toolbox sends each call to the tool set that has it.
        box = Toolbox([ChartTool(), Other(), None])
        assert [d["function"]["name"] for d in box.definitions] == ["create_chart", "query"]
        assert asyncio.run(box.run("query", {}))[0] == "rows"
        assert asyncio.run(box.run("nope", {}))[0].startswith("Error:")
        assert not Toolbox([])

    print("Agent skills: charts, gates, switches, memories, memory tools and the toolbox all pass.")


if __name__ == "__main__":
    main()
