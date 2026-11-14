# Document tooling

Regenerates the two documents in `docs/` from the running app, so they always match the platform.

```powershell
.\.venv\Scripts\python.exe -m pip install python-docx python-pptx playwright Pillow pymupdf
.\run.ps1                                                    # the app must be running on port 8000
.\.venv\Scripts\python.exe tools\capture_screens.py          # screenshots of every card block and panel (uses installed Edge)
.\.venv\Scripts\python.exe tools\build_guide.py              # docs\Dashboard User Guide.docx
.\.venv\Scripts\python.exe tools\build_deck.py               # docs\Leadership Briefing.pptx
powershell -ExecutionPolicy Bypass -File tools\render_office.ps1 -Docx "docs\Dashboard User Guide.docx" -OutDir data\docs_build\render
```

- `guide_content.py` holds the wording for every card and graph; edit it and rebuild.
- `build_deck.py` reads its figures live from the lakehouse and the API, writes a rough preview image per slide to
  `data/docs_build/render/slides/` and reports any text that would not fit its box. Charts in the deck are native
  PowerPoint charts and appear as outlined placeholders in those previews.
- `render_office.ps1` uses Word to update the table of contents and export a review PDF. It runs Word in its own
  hidden process and does not touch PowerPoint.
