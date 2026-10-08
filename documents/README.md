# documents/

Everything behind the Word documents page except its HTTP routes.

```
document_engine/
  engine/     works on Word files (python-docx). No model.
    wordfile.py   open a file, number paragraphs and text boxes, find blanks, fill them
    wordedit.py   the fixed list of edit operations; Markdown -> Word
    slotmap.py    labels of a designed template -> its slot map
    render.py     fill a designed template, copying or removing repeated blocks
  model/      the model's part. It decides; it never writes a file.
    prompts.py    every instruction the model gets, and the JSON it must answer in
    service.py    sends them to the local model and checks what comes back
```

The Invoices tab has a folder of its own, with the same split:

```
document_engine/invoices/
  reader.py     a file (PDF, Word, photo) -> pages: text and/or a picture. No model.
  checks.py     cleans each value, checks it (GSTIN check digit, dates, total against
                the amount in words), scores the Confidence. No model.
  export.py     the reviewed rows -> .xlsx or .csv. No model.
  prompts.py    the model's part: what it is told, and the JSON it must answer in
  service.py    the model's part: one page at a time to the local model
read_invoices.py   the same from a terminal:
                   cd backend && python ../documents/read_invoices.py <folder> --out out.xlsx
```

- HTTP routes and file storage: `backend/app/documents/` (`invoices.py` for the Invoices tab)
- The Invoices page: `frontend/invoices.html`; its settings: the `invoices:` block in `models.yaml`
- The page: `frontend/documents.html`
- Settings and the model to use: the `documents:` block in `ollama/config/models.yaml`
