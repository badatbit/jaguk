# Windows OCR (WinRT) — 이미지 목록을 읽어 줄 단위 텍스트·상자를 JSON 으로 출력.
# typelet extract 가 부른다. 단독 사용:
#   powershell -NoProfile -ExecutionPolicy Bypass -File winocr.ps1 `
#     -ListPath files.txt -Root F:\imgs -OutputPath ocr.json -Lang ja
param(
    [Parameter(Mandatory)][string]$ListPath,    # 절대 경로 목록 (한 줄 하나, UTF-8)
    [Parameter(Mandatory)][string]$Root,        # 출력 file 키를 이 루트 기준 상대로
    [Parameter(Mandatory)][string]$OutputPath,
    [string]$Lang = "ja"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime

function Await-WinRt($Operation, [Type]$ResultType) {
    $method = (
        [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq "AsTask" -and
            $_.IsGenericMethod -and
            $_.GetParameters().Count -eq 1
        }
    )[0]
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.Wait()
    return $task.Result
}

[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime] | Out-Null

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage(
    [Windows.Globalization.Language]::new($Lang)
)
if ($null -eq $engine) {
    Write-Error ("OCR 엔진 생성 실패: 언어 '$Lang' 의 Windows OCR 언어팩이 " +
        "설치돼 있지 않습니다 (설정 > 시간 및 언어 > 언어 에서 추가).")
    exit 1
}

$rootFull = (Resolve-Path -LiteralPath $Root).Path
$files = Get-Content -LiteralPath $ListPath -Encoding UTF8 |
    Where-Object { $_.Trim() -ne "" }

$result = foreach ($path in $files) {
    $item = Get-Item -LiteralPath $path
    $storageFile = Await-WinRt (
        [Windows.Storage.StorageFile]::GetFileFromPathAsync($item.FullName)
    ) ([Windows.Storage.StorageFile])
    $stream = Await-WinRt (
        $storageFile.OpenReadAsync()
    ) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
    try {
        $decoder = Await-WinRt (
            [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
        ) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await-WinRt (
            $decoder.GetSoftwareBitmapAsync()
        ) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $ocr = Await-WinRt (
            $engine.RecognizeAsync($bitmap)
        ) ([Windows.Media.Ocr.OcrResult])

        $lines = foreach ($line in $ocr.Lines) {
            $words = @($line.Words)
            if ($words.Count -eq 0) { continue }
            $x0 = ($words | ForEach-Object { $_.BoundingRect.X } | Measure-Object -Minimum).Minimum
            $y0 = ($words | ForEach-Object { $_.BoundingRect.Y } | Measure-Object -Minimum).Minimum
            $x1 = ($words | ForEach-Object { $_.BoundingRect.X + $_.BoundingRect.Width } | Measure-Object -Maximum).Maximum
            $y1 = ($words | ForEach-Object { $_.BoundingRect.Y + $_.BoundingRect.Height } | Measure-Object -Maximum).Maximum
            [ordered]@{
                text = (($words | ForEach-Object { $_.Text }) -join "")
                x = [int][Math]::Floor($x0)
                y = [int][Math]::Floor($y0)
                w = [int][Math]::Ceiling($x1 - $x0)
                h = [int][Math]::Ceiling($y1 - $y0)
            }
        }
        [ordered]@{
            file = $item.FullName.Substring($rootFull.Length + 1).Replace("\", "/")
            width = [int]$decoder.PixelWidth
            height = [int]$decoder.PixelHeight
            lines = @($lines)
        }
    }
    finally {
        $stream.Dispose()
    }
}

$json = @($result) | ConvertTo-Json -Depth 6
if ($json -notmatch "^\s*\[") { $json = "[$json]" }   # 파일 1개여도 배열로
[System.IO.File]::WriteAllText($OutputPath, $json, [System.Text.UTF8Encoding]::new($false))
Write-Host "saved $(@($result).Count) files -> $OutputPath"
