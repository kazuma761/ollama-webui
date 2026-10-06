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

- HTTP routes and file storage: `backend/app/documents/`
- The page: `frontend/documents.html`
- Settings and the model to use: the `documents:` block in `ollama/config/models.yaml`
