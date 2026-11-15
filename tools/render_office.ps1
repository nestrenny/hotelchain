# Finalises and renders the generated Office files using the installed Word.
#   Word:       updates the table of contents, saves, and writes a PDF for review.
# Safe to run while you have other documents open: Word runs in its own hidden process.
param([string]$Docx, [string]$Pptx, [string]$OutDir, [switch]$PdfOnly)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force $OutDir | Out-Null
$out = (Resolve-Path $OutDir).Path
if ($Docx -and $PdfOnly) {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    try {
        $doc = $word.Documents.Open((Resolve-Path $Docx).Path, $false, $true, $false)
        [void]$doc.ComputeStatistics(2)          # lay out every page first; exporting before that stalls
        $doc.ExportAsFixedFormat((Join-Path $out "guide.pdf"), 17, $false, 1)   # 1 = optimise for on-screen
        $doc.Close($false)
    } finally { $word.Quit() }
    exit 0
}
if ($Docx) {
    $before = @(Get-Process WINWORD -ErrorAction SilentlyContinue | ForEach-Object Id)
    $word = New-Object -ComObject Word.Application
    $mine = @(Get-Process WINWORD -ErrorAction SilentlyContinue | Where-Object { $before -notcontains $_.Id } | ForEach-Object Id)
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
    } finally { $word.Quit() }
    # Quit returns before Word has gone; a new Word started in that window attaches to the closing one and hangs.
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word)
    foreach ($id in $mine) { try { Wait-Process -Id $id -Timeout 30 -ErrorAction Stop } catch {} }
    # The PDF is exported from a copy, by a separate PowerShell process with its own Word: exporting from
    # the session that just edited and saved the file stalls with this many images.
    Write-Host "word: pdf"
    $copy = Join-Path $out "guide_copy.docx"
    Copy-Item (Resolve-Path $Docx).Path $copy -Force
    $p = Start-Process powershell -ArgumentList "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Docx", "`"$copy`"", "-OutDir", "`"$out`"", "-PdfOnly" -PassThru -WindowStyle Hidden
    if (-not $p.WaitForExit(300000)) {
        # Known problem: this export sometimes stalls. The document itself is already updated and saved.
        $p.Kill()
        Get-Process WINWORD -ErrorAction SilentlyContinue | Where-Object { $before -notcontains $_.Id } | ForEach-Object { Stop-Process -Id $_.Id -Force }
        Write-Warning "The review PDF was not written (Word did not finish in five minutes). The document is saved with its contents updated."
    } else { Write-Host "word: done" }
}
if ($Pptx) {
    # PowerPoint is single-instance, so automating it would act inside any presentation you have open.
    # The deck is therefore checked with tools/build_deck.py (text-fit check and preview images) instead.
    Write-Host "powerpoint: not automated; see the preview images written by tools/build_deck.py"
}
