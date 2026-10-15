# Runs the nightly AI review. Called by the "InsideTrader AI Review" scheduled task.
$ErrorActionPreference = 'Continue'  # native stderr must not abort the run
$repo = Resolve-Path (Join-Path $PSScriptRoot '..\..\..')
$python = 'C:\Users\USER-PC\AppData\Local\Programs\Python\Python311\python.exe'
$logDir = 'D:\Personal\InsideTrader-ai-review\logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("{0:yyyy-MM-dd}.log" -f (Get-Date))

$env:PYTHONIOENCODING = 'utf-8'
Set-Location $repo
& $python (Join-Path $PSScriptRoot 'ai_review.py') @args 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
exit $LASTEXITCODE
