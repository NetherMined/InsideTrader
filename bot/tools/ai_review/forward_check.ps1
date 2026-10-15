# Forward-test check-in for the path model (FIX_PLAN_OCT14 8.2d). Run by the
# "InsideTrader Forward Check" scheduled tasks; shows a pop-up and appends to a log.
# Live paper trades only. The full pass/fail review also needs the nightly baseline (ask Claude).
$ErrorActionPreference = 'Continue'
$since = '2026-10-15 13:26:00+00'
$sql = @"
SELECT count(*), coalesce(sum((pnl_usdt > 0)::int), 0),
       round(coalesce(sum(pnl_usdt), 0)::numeric, 2),
       round(coalesce(avg(100 * pnl_usdt / nullif(quantity * entry_price, 0)), 0)::numeric, 4),
       round(coalesce(1.96 * stddev(100 * pnl_usdt / nullif(quantity * entry_price, 0)) / sqrt(nullif(count(*), 0)), 0)::numeric, 4),
       count(DISTINCT date(opened_at))
FROM trades
WHERE status = 'CLOSED' AND opened_at >= '$since'
  AND extra->'entry_indicators'->>'entry_model' = 'path'
"@
$row = docker exec insidetrader-postgres-1 psql -U insidetrader -d insidetrader -tA -F '|' -c $sql
if (-not $row) {
    $text = "Forward check: could not read the database (is Docker running?)."
} else {
    $n, $wins, $net, $avg, $ci, $days = $row.Trim().Split('|')
    $low = [double]$avg - [double]$ci
    $high = [double]$avg + [double]$ci
    if ([int]$days -ge 7 -and $high -lt 0) {
        $verdict = 'FAIL rule hit: whole 95% range below zero. Consider ENTRY_MODEL=ensemble.'
    } elseif ([int]$days -ge 14 -and [int]$n -ge 500 -and $low -gt 0 -and [double]$net -gt 0) {
        $verdict = 'PASS on live paper (also check the baseline sim before Phase 6).'
    } else {
        $verdict = 'Undecided so far: keep running.'
    }
    $winRate = if ([int]$n -gt 0) { [math]::Round(100 * [int]$wins / [int]$n, 1) } else { 0 }
    $text = "Path model forward test: $n closed trades over $days day(s), win $winRate%, net `$$net, " +
            "avg $avg% per trade (95% range $([math]::Round($low,4)) to $([math]::Round($high,4))).`n$verdict`n" +
            "Ask Claude for the full 8.2d review (adds the baseline sim + AI reports)."
}
$logDir = 'D:\Personal\InsideTrader-ai-review\logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
"$(Get-Date -Format 'yyyy-MM-dd HH:mm') $text" | Out-File -FilePath (Join-Path $logDir 'forward_check.log') -Append -Encoding utf8
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.MessageBox]::Show($text, 'InsideTrader forward test') | Out-Null
