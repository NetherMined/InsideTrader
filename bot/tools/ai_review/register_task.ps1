# Registers the nightly AI review as a Windows scheduled task for the current user.
# 04:45 local (SAST) = 02:45 UTC, 30 min after the bot's 02:15 UTC baseline job.
# StartWhenAvailable runs a missed review at the next boot or login.
$script = Join-Path $PSScriptRoot 'ai_review.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At '04:45'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName 'InsideTrader AI Review' -Action $action -Trigger $trigger `
    -Settings $settings -Description 'Reviews the bot hindsight baseline with Muse Spark (fallback gpt-oss:20b)' -Force
