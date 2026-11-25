# Document tooling

Regenerates the documents in `docs/` and the Power BI report from the running app and the lakehouse, so they always match the platform.

```powershell
.\.venv\Scripts\python.exe -m pip install python-docx python-pptx playwright Pillow pymupdf
.\run.ps1                                                    # the app must be running on port 8000
.\.venv\Scripts\python.exe tools\capture_screens.py          # screenshots of every card block and panel (uses installed Edge)
.\.venv\Scripts\python.exe tools\build_guide.py              # docs\Dashboard User Guide.docx
.\.venv\Scripts\python.exe tools\build_deck.py               # docs\Leadership Briefing.pptx
.\.venv\Scripts\python.exe -m pip install imageio-ffmpeg piper-tts
.\.venv\Scripts\python.exe tools\build_video.py              # docs\Luxury Hotel Chain Platform Explainer.mp4 and .srt
powershell -ExecutionPolicy Bypass -File tools\render_office.ps1 -Docx "docs\Dashboard User Guide.docx" -OutDir data\docs_build\render
```

- `guide_content.py` holds the wording for every card and graph; edit it and rebuild.
- `build_deck.py` reads its figures live from the lakehouse and the API, writes a rough preview image per slide to
  `data/docs_build/render/slides/` and reports any text that would not fit its box. Charts in the deck are native
  PowerPoint charts and appear as outlined placeholders in those previews.
- `render_office.ps1` uses Word to update the table of contents and export a review PDF. The PDF step sometimes stalls; the script then warns, and the document itself is still saved with its contents updated. It runs Word in its own
  hidden process and does not touch PowerPoint.
- `build_video.py` holds the storyline and narration in its `STORY` list: one line per shot, with the page, the area to focus on, the caption and
  the words spoken. It captures each page at full length, moves a virtual camera over it (zooming to and highlighting each focus area in time
  with the narration), and encodes with the ffmpeg bundled in `imageio-ffmpeg`. Narration uses the offline Piper neural voice
  `en_US-ryan-high`; install it with `pip install piper-tts` and
  `python -m piper.download_voices en_US-ryan-high --download-dir data/voices`. No text is sent to an online service.
  Add `--no-capture` to reuse the last captures.
- `build_powerbi.py` exports the Gold and AI tables to Parquet and writes the Power BI project in `powerbi/`: the tables, relationships and
  measures (TMDL) and the nine report pages (PBIR). `--validate` checks the report files against Microsoft's published schemas.
- `build_schema_diagram.py` reads `powerbi/model.json` and writes `docs/Database Schema.drawio`, so the diagram always matches the model.
  Run it after `build_powerbi.py`.
