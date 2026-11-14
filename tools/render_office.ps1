# Finalises and renders the generated Office files using the installed Word and PowerPoint.
#   Word:       updates the table of contents, saves, and writes a PDF for review.
# Safe to run while you have other documents open: Word runs in its own hidden process.
param([string]$Docx, [string]$Pptx, [string]$OutDir)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force $OutDir | Out-Null
$out = (Resolve-Path $OutDir).Path
if ($Docx) {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    try {
        Write-Host "word: open"
        $doc = $word.Documents.Open((Resolve-Path $Docx).Path, $false, $false, $false)
        Write-Host "word: update contents"
        foreach ($toc in $doc.TablesOfContents) { $toc.Update() }
        Write-Host "word: save"
        $doc.Save()
        $doc.Close($false)
        # Export from a fresh read-only copy: exporting straight after an edit-and-save stalls with this many images.
        Write-Host "word: pdf"
        $doc = $word.Documents.Open((Resolve-Path $Docx).Path, $false, $true, $false)
        $doc.ExportAsFixedFormat((Join-Path $out "guide.pdf"), 17, $false, 1)   # 1 = optimise for on-screen
        $doc.Close($false)
        Write-Host "word: done"
    } finally { $word.Quit() }
}
if ($Pptx) {
    # PowerPoint is single-instance, so automating it would act inside any presentation you have open.
    # The deck is therefore checked with tools/build_deck.py (text-fit check and preview images) instead.
    Write-Host "powerpoint: not automated; see the preview images written by tools/build_deck.py"
}
