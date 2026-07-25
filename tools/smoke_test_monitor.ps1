param(
    [ValidateRange(30, 1800)]
    [int]$Seconds = 30
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python -ErrorAction Stop).Source
$managerPath = Join-Path $projectDir "gerenciador.pyw"
$configPath = Join-Path $projectDir "sistema\config.json"
$go2rtcPath = Join-Path $projectDir "sistema\go2rtc\go2rtc.exe"
$ffmpegPath = Join-Path $projectDir "sistema\go2rtc\ffmpeg.exe"
$stdoutPath = Join-Path $env:TEMP "nvr-smoke-stdout.txt"
$stderrPath = Join-Path $env:TEMP "nvr-smoke-stderr.txt"

function Get-ProjectProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.Name -in @("python.exe", "pythonw.exe", "go2rtc.exe", "ffmpeg.exe")) -and
            (
                $_.CommandLine -match [regex]::Escape($managerPath) -or
                $_.ExecutablePath -eq $go2rtcPath -or
                $_.ExecutablePath -eq $ffmpegPath
            )
        }
}

function Get-ProcessTreeIds {
    param(
        [int]$RootProcessId,
        [object[]]$Processes
    )

    $ids = [System.Collections.Generic.HashSet[int]]::new()
    [void]$ids.Add($RootProcessId)
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($process in $Processes) {
            if (
                $ids.Contains([int]$process.ParentProcessId) -and
                $ids.Add([int]$process.ProcessId)
            ) {
                $changed = $true
            }
        }
    }
    return @($ids)
}

function Get-HealthSnapshot {
    $raw = & $python $managerPath "--health-check"
    try {
        return ($raw | ConvertFrom-Json)
    } catch {
        throw "Falha ao interpretar --health-check (exit $LASTEXITCODE)."
    }
}

if (Get-ProjectProcesses) {
    throw "Ja existe uma instancia do NVR ou processo de midia deste projeto."
}

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$storageRoot = [string]$config.gdrive_root
if (-not $storageRoot) {
    throw "O destino principal nao esta configurado."
}

$cameraFolders = @(
    $config.storage_folder_map.PSObject.Properties |
        ForEach-Object { [string]$_.Value } |
        Select-Object -Unique
)
$cameraDirs = @($cameraFolders | ForEach-Object { Join-Path $storageRoot $_ })
$storageDriveName = [System.IO.Path]::GetPathRoot($storageRoot)
$baseline = Get-HealthSnapshot
$baselineKernel144 = [int]$baseline.metrics.kernel_144_reports_24h
$startedAt = Get-Date

Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
$process = Start-Process `
    -FilePath $python `
    -ArgumentList "`"$managerPath`" --smoke-test-seconds $Seconds" `
    -WorkingDirectory $projectDir `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -WindowStyle Hidden `
    -PassThru

$maxMemoryMb = 0.0
$maxThreads = 0
$maxCpuPercent = 0.0
$maxTemporaryBytes = 0L
$lastTemporaryBytes = $null
$growthObserved = $false
$lastGrowthAt = $null
$stopReason = $null
$deadline = (Get-Date).AddSeconds($Seconds + 45)
$processorCount = [Environment]::ProcessorCount
$lastCpuSeconds = $null
$lastCpuSampleAt = Get-Date

do {
    Start-Sleep -Seconds 2
    $allProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $treeIds = Get-ProcessTreeIds -RootProcessId $process.Id -Processes $allProcesses
    $memoryMb = 0.0
    $threadCount = 0
    $cpuSeconds = 0.0
    foreach ($processId in $treeIds) {
        $observed = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($observed) {
            $memoryMb += $observed.WorkingSet64 / 1MB
            $threadCount += $observed.Threads.Count
            $cpuSeconds += $observed.TotalProcessorTime.TotalSeconds
        }
    }
    $maxMemoryMb = [math]::Max($maxMemoryMb, $memoryMb)
    $maxThreads = [math]::Max($maxThreads, $threadCount)
    $cpuSampleAt = Get-Date
    $cpuElapsed = ($cpuSampleAt - $lastCpuSampleAt).TotalSeconds
    if (
        $null -ne $lastCpuSeconds -and
        $cpuElapsed -gt 0 -and
        $cpuSeconds -ge $lastCpuSeconds
    ) {
        $cpuPercent = (
            ($cpuSeconds - $lastCpuSeconds) /
            $cpuElapsed /
            $processorCount *
            100
        )
        $maxCpuPercent = [math]::Max($maxCpuPercent, $cpuPercent)
    }
    $lastCpuSeconds = $cpuSeconds
    $lastCpuSampleAt = $cpuSampleAt

    $temporaryBytes = 0L
    foreach ($cameraDir in $cameraDirs) {
        $temporaryDir = Join-Path $cameraDir ".gravando_temp"
        if (Test-Path -LiteralPath $temporaryDir) {
            $measured = Get-ChildItem `
                -LiteralPath $temporaryDir `
                -File `
                -Force `
                -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name.EndsWith(".recording") } |
                    Measure-Object -Property Length -Sum
            if ($measured.Sum) {
                $temporaryBytes += [long]$measured.Sum
            }
        }
    }
    if ($null -ne $lastTemporaryBytes -and $temporaryBytes -ne $lastTemporaryBytes) {
        $growthObserved = $true
        $lastGrowthAt = Get-Date
    }
    $lastTemporaryBytes = $temporaryBytes
    $maxTemporaryBytes = [math]::Max($maxTemporaryBytes, $temporaryBytes)

    $storageDrive = [System.IO.DriveInfo]::GetDrives() |
        Where-Object { $_.Name -eq $storageDriveName }
    if (-not $storageDrive -or -not $storageDrive.IsReady) {
        $stopReason = "HD desconectado"
    }
    if ($memoryMb -gt 750) {
        $stopReason = "memoria acima de 750 MB"
    }
    if (
        $growthObserved -and
        $temporaryBytes -gt 0 -and
        $null -ne $lastGrowthAt -and
        ((Get-Date) - $lastGrowthAt).TotalSeconds -ge 15
    ) {
        $stopReason = "gravacao parou de crescer por 15 segundos"
    }
    if ($stopReason) {
        & $python $managerPath "--safe-stop" | Out-Null
        break
    }

    $process.Refresh()
} while (-not $process.HasExited -and (Get-Date) -lt $deadline)

$process.Refresh()
if (-not $process.HasExited -and $stopReason) {
    [void]$process.WaitForExit(15000)
    $process.Refresh()
}
if (-not $process.HasExited) {
    $stopReason = "timeout do ensaio"
    & $python $managerPath "--safe-stop" | Out-Null
    [void]$process.WaitForExit(15000)
}
if ($process.HasExited) {
    $process.WaitForExit()
}
$exitCode = if ($process.HasExited) { [int]$process.ExitCode } else { $null }
Start-Sleep -Seconds 2

$newVideos = @()
$artifacts = @()
foreach ($cameraDir in $cameraDirs) {
    if (-not (Test-Path -LiteralPath $cameraDir)) {
        continue
    }
    $newVideos += Get-ChildItem `
        -LiteralPath $cameraDir `
        -Recurse `
        -File `
        -Filter "*.ts" `
        -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -ge $startedAt }
    $artifacts += Get-ChildItem `
        -LiteralPath $cameraDir `
        -Recurse `
        -File `
        -Force `
        -ErrorAction SilentlyContinue |
            Where-Object {
                $_.LastWriteTime -ge $startedAt -and
                $_.Name -match "\.(recording|finalizing|syncing|recovering)$"
            }
}

$invalidVideos = @()
$validationAvailable = Test-Path -LiteralPath $ffmpegPath
if ($validationAvailable) {
    foreach ($video in $newVideos) {
        $previousErrorAction = $ErrorActionPreference
        try {
            $ErrorActionPreference = "SilentlyContinue"
            & $ffmpegPath `
                "-v" "error" `
                "-i" $video.FullName `
                "-t" "5" `
                "-f" "null" "NUL" 2>$null
            $validationExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorAction
        }
        if ($validationExitCode -ne 0) {
            $invalidVideos += $video.FullName
        }
    }
}

$finalHealth = Get-HealthSnapshot
$finalKernel144 = [int]$finalHealth.metrics.kernel_144_reports_24h
$residualProcesses = @(Get-ProjectProcesses)
$newVideoBytes = ($newVideos | Measure-Object -Property Length -Sum).Sum
$recordingActivityConfirmed = (
    $growthObserved -or
    (
        $newVideos.Count -ge $cameraDirs.Count -and
        $newVideoBytes -gt 0
    )
)
$stderrTail = @()
if (Test-Path -LiteralPath $stderrPath) {
    $stderrTail = @(
        Get-Content -LiteralPath $stderrPath -Tail 20 |
            ForEach-Object { [string]$_ }
    )
}

$summary = [ordered]@{
    duration_seconds = [math]::Round(((Get-Date) - $startedAt).TotalSeconds, 1)
    exit_code = $exitCode
    stop_reason = $stopReason
    max_process_tree_mb = [math]::Round($maxMemoryMb, 1)
    max_threads = $maxThreads
    max_cpu_percent = [math]::Round($maxCpuPercent, 1)
    max_temporary_bytes = $maxTemporaryBytes
    growth_observed = $growthObserved
    recording_activity_confirmed = $recordingActivityConfirmed
    new_video_count = $newVideos.Count
    new_video_bytes = $newVideoBytes
    validation_available = $validationAvailable
    invalid_video_count = $invalidVideos.Count
    new_kernel_144 = [math]::Max(0, $finalKernel144 - $baselineKernel144)
    storage_free_gb = $finalHealth.metrics.hd_free_gb
    artifact_count = $artifacts.Count
    residual_process_count = $residualProcesses.Count
    stderr_tail = $stderrTail
}
$summary | ConvertTo-Json -Depth 4
$newVideos |
    Select-Object FullName, Length, LastWriteTime |
    Format-Table -AutoSize

$failed = (
    $null -ne $stopReason -or
    $exitCode -ne 0 -or
    -not $recordingActivityConfirmed -or
    $newVideos.Count -lt $cameraDirs.Count -or
    -not $validationAvailable -or
    $invalidVideos.Count -ne 0 -or
    $finalKernel144 -gt $baselineKernel144 -or
    $artifacts.Count -ne 0 -or
    $residualProcesses.Count -ne 0
)
if ($failed) {
    exit 3
}
