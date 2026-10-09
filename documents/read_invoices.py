"""Reads invoices from a terminal: files or a folder in, a sheet out.

The same reading as the Invoices tab of the documents page, without the
browser. Use it to try a model on a folder of invoices, or to compare two.

    python ../documents/read_invoices.py ~/invoices                    prints what it reads
    python ../documents/read_invoices.py ~/invoices --out march.xlsx   and writes the sheet
    python ../documents/read_invoices.py ~/invoices --model gemma4:12b --out gemma.csv

Run it from backend/ with the Python that has the project's packages
(with uv: `uv run --inexact python ../documents/read_invoices.py ...`).

  --model  an id from models.yaml or the tag of a pulled Ollama model;
           left out, the one in `invoices.model` is used
  --out    .xlsx, .csv, or .json (everything, with the level and note of each value)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for folder in (HERE, HERE.parent / "ollama"):  # lets it run from a checkout, without installing
    if str(folder) not in sys.path:
        sys.path.append(str(folder))

from document_engine.invoices import ACCEPTED, InvoiceService, to_csv, to_xlsx  # noqa: E402
from document_engine.invoices.export import Options  # noqa: E402
from ollama_pipeline import PipelineError, Registry, UnsupportedDocument, load_config  # noqa: E402


def gather(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for path in map(Path, paths):
        found = sorted(p for p in path.iterdir() if p.is_file()) if path.is_dir() else [path]
        files += [p for p in found if p.suffix.lower() in ACCEPTED and not p.name.startswith(".")]
    return files


async def run(args: argparse.Namespace) -> int:
    files = gather(args.paths)
    if not files:
        print(f"No invoices found. Accepted: {', '.join(ACCEPTED)}")
        return 1
    registry = Registry(load_config())
    service = InvoiceService(registry)
    try:
        default, models = await service.models()
        model = args.model or default
        # A tag such as gemma4:12b is as good as an id from models.yaml.
        model = next((m["id"] for m in models if model in (m["id"], m["model"], m["model"].removesuffix(":latest"))), model)
        chosen = next((m for m in models if m["id"] == model), None)
        if not chosen or not chosen["available"]:
            print(f"Model '{model}' is not available. Local models: " + ", ".join(m["model"] for m in models if m["available"]))
            return 1
        print(f"Model: {chosen['label']} ({chosen['model']}), " + ("reads pictures" if chosen["vision"] else "text only: scans and photos are skipped"))
        print(f"Files: {len(files)}\n")

        rows, started = [], time.monotonic()
        for file in files:
            print(f"{file.name}")
            try:
                async for event in service.read(model, file.name, file.read_bytes()):
                    if event["type"] == "rows":
                        rows += event["rows"]
                        for row in event["rows"]:
                            v = row["values"]
                            print(f"   {row['confidence']:.2f}  {row['kind']:<8} no. {v['InvoiceId'] or '-':<20} {v['InvoiceDate'] or '-':<12} "
                                  f"{v['InvoiceTotal'] or '-':>12}  {v['VendorName'] or '-'}  ->  {v['CustomerName'] or '-'}")
                            if v["TotalTaxAmount"] or v["TaxableValue"]:
                                print(f"         taxable {v['TaxableValue'] or '-'} + tax {v['TotalTaxAmount'] or '-'} "
                                      f"(C {v['CGSTAmount'] or '-'}, S {v['SGSTAmount'] or '-'}, I {v['IGSTAmount'] or '-'})")
                            for number, item in enumerate(row["items"], 1):
                                line = item["values"]
                                if any(line.values()):
                                    print(f"         {number:>2}. {line['Description'][:48]:<48} qty {line['Qty'] or '-':<8} "
                                          f"at {line['UnitPrice'] or '-':>10} = {line['UnitAmount'] or '-':>11}")
                            for field, note in row["notes"].items():
                                if row["levels"][field] != "ok":
                                    print(f"         {field}: {note}")
                    elif event["type"] == "notice":
                        print(f"   ! {event['message']}")
                    elif event["type"] == "done":
                        print(f"   {event['pages']} page(s), {event['seconds']} s")
            except (PipelineError, UnsupportedDocument) as exc:
                print(f"   ! {exc}")
        seconds = time.monotonic() - started
        doubtful = sum(level == "check" for row in rows for level in row["levels"].values())
        lines = sum(len(row["items"]) for row in rows)
        print(f"\n{len(rows)} document(s), {lines} row(s) in the sheet, from {len(files)} file(s) in {seconds:.0f} s; {doubtful} value(s) to check.")

        if args.out:
            out = Path(args.out)
            kind = out.suffix.lower()
            if kind == ".csv":
                out.write_bytes(to_csv(rows))
            elif kind == ".json":
                out.write_text(json.dumps({"model": chosen["model"], "seconds": round(seconds, 1), "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
            else:
                out.write_bytes(to_xlsx(rows, service.config.date_format, Options(checks=True, summary=True)))
            print(f"Written to {out}")
        return 0
    finally:
        await registry.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", help="invoice files, or folders of them")
    parser.add_argument("--model", default="")
    parser.add_argument("--out", default="")
    sys.exit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
