<#
mic-monitor installer for Windows.

Install or upgrade (PowerShell):

    irm https://raw.githubusercontent.com/robworks-code/mic-monitor/main/install.ps1 | iex

Uninstall:

    & ([scriptblock]::Create((irm https://raw.githubusercontent.com/robworks-code/mic-monitor/main/install.ps1))) -Uninstall

What it does:

  1. Finds Python 3.10 or newer. If there is none, installs Python 3.12 with winget.
  2. Creates a private virtual environment in %LOCALAPPDATA%\mic-monitor\venv
     (recreated on every run, so running the line again upgrades).
  3. Installs mic-monitor into it from GitHub (no git needed).
  4. Copies the two commands, mic-monitor and mic-monitor-tray, to
     %LOCALAPPDATA%\mic-monitor\bin and adds that folder to your user PATH.
  5. Adds a Start Menu shortcut "Mic Monitor" and starts the tray icon.

Uninstall stops mic-monitor and removes everything above, plus the tray's
Start at login entry if it was turned on. Your saved settings
(%APPDATA%\mic-monitor) and logs (%LOCALAPPDATA%\mic-monitor\*.log) are kept.

Options: -Uninstall, -NoLaunch (do not start the tray icon),
-Source <path or URL> (install from a local checkout or another archive;
the MIC_MONITOR_SOURCE environment variable does the same).
#>
param(
    [switch]$Uninstall,
    [switch]$NoLaunch,
    [string]$Source = $(if ($env:MIC_MONITOR_SOURCE) { $env:MIC_MONITOR_SOURCE }
                        else { 'https://github.com/robworks-code/mic-monitor/archive/refs/heads/main.zip' })
)

$ErrorActionPreference = 'Stop'
# Non-zero exit codes from python, pip and winget are checked by hand below.
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
}

if (-not $env:LOCALAPPDATA -or -not $env:APPDATA) {
    throw 'LOCALAPPDATA or APPDATA is not set; cannot decide where to install.'
}

$Root = Join-Path $env:LOCALAPPDATA 'mic-monitor'
$Venv = Join-Path $Root 'venv'
$Bin = Join-Path $Root 'bin'
$Icon = Join-Path $Root 'mic-monitor.ico'
$Shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Mic Monitor.lnk'
# Name used by earlier versions; removed on install and uninstall.
$OldShortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\mic-monitor tray.lnk'
$Apps = @('mic-monitor.exe', 'mic-monitor-tray.exe')
$SelfUrl = 'https://raw.githubusercontent.com/robworks-code/mic-monitor/main/install.ps1'

function Say([string]$Text) { Write-Host $Text }

function Remove-Owned([string]$Path) {
    # Only ever delete inside our own folder, and never the folder itself
    # (it also holds the worker log and the saved status).
    if (-not $Path.StartsWith("$Root\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "refusing to delete outside $Root : $Path"
    }
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Recurse -Force }
}

function Stop-MicMonitor {
    # Stop the background worker through the CLI when we have one, so it goes
    # through the PID file, then kill anything left that runs from this install
    # or is a mic-monitor tray or worker from another install. Installing over
    # a running exe fails on Windows, so this has to be thorough.
    $cli = Join-Path $Bin 'mic-monitor.exe'
    if (Test-Path -LiteralPath $cli) {
        try { & $cli stop --keep-state 2>&1 | Out-Null } catch { }
    }
    $procs = Get-CimInstance Win32_Process | Where-Object {
        ($_.ExecutablePath -and $_.ExecutablePath.StartsWith("$Root\", [StringComparison]::OrdinalIgnoreCase)) -or
        ($_.Name -in $Apps) -or
        ($_.CommandLine -and ($_.CommandLine -like '*mic_monitor.worker*' -or $_.CommandLine -like '*mic_monitor.tray*'))
    }
    foreach ($p in $procs) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($procs) { Start-Sleep -Milliseconds 500 }
}

function Get-PythonInfo([string]$Exe, [string[]]$PreArgs) {
    # Returns @(path, [version]) or $null. The Microsoft Store "python"
    # alias stub prints a message and exits non-zero, so it fails this test.
    # No quotes in the Python code: Windows PowerShell 5.1 drops embedded
    # double quotes from native command arguments.
    $local:ErrorActionPreference = 'Continue'
    try {
        $code = 'import sys; print(sys.executable); print(sys.version_info[0]); print(sys.version_info[1])'
        $out = & $Exe @($PreArgs + @('-c', $code)) 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $lines = @($out | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
        if ($lines.Count -lt 3) { return $null }
        return @($lines[0], [version]"$($lines[1]).$($lines[2])")
    } catch { return $null }
}

function Find-Python {
    $candidates = New-Object System.Collections.ArrayList
    foreach ($name in @('py', 'python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) {
            $pre = @(); if ($name -eq 'py') { $pre = @('-3') }
            [void]$candidates.Add(@{ Exe = $cmd.Source; Pre = $pre })
        }
    }
    # A Python winget just installed may not be on this session's PATH yet.
    $dirs = @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles\Python*", "${env:ProgramFiles(x86)}\Python*")
    foreach ($exe in (Get-ChildItem -Path $dirs -Filter 'python.exe' -Recurse -Depth 1 -ErrorAction SilentlyContinue |
                      Sort-Object FullName -Descending)) {
        [void]$candidates.Add(@{ Exe = $exe.FullName; Pre = @() })
    }
    foreach ($c in $candidates) {
        $info = Get-PythonInfo $c.Exe $c.Pre
        if ($info -and $info[1] -ge [version]'3.10') { return $info[0] }
    }
    return $null
}

function Install-Python {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    $manual = 'Install Python 3.12 or newer from https://www.python.org/downloads/ and run this installer again.'
    if (-not $winget) { throw "Python 3.10 or newer was not found and winget is not available. $manual" }
    Say 'Python 3.10 or newer was not found. Installing Python 3.12 with winget (about a minute)...'
    & $winget.Source install --exact --id Python.Python.3.12 --silent --disable-interactivity `
        --accept-package-agreements --accept-source-agreements | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "winget could not install Python (exit code $LASTEXITCODE). $manual" }
    $python = Find-Python
    if (-not $python) { throw "Python was installed but could not be found afterwards. Open a new PowerShell window and run this installer again." }
    return $python
}

function Remove-FromUserPath {
    $parts = @([Environment]::GetEnvironmentVariable('Path', 'User') -split ';' | Where-Object { $_ })
    if ($parts -contains $Bin) {
        $kept = @($parts | Where-Object { $_ -ne $Bin })
        [Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User')
    }
}

function Add-ToUserPath {
    $parts = @([Environment]::GetEnvironmentVariable('Path', 'User') -split ';' | Where-Object { $_ })
    $added = $false
    if ($parts -notcontains $Bin) {
        [Environment]::SetEnvironmentVariable('Path', (($parts + $Bin) -join ';'), 'User')
        $added = $true
    }
    if (@($env:Path -split ';') -notcontains $Bin) { $env:Path = "$env:Path;$Bin" }
    return $added
}

# ---------------------------------------------------------------- uninstall

if ($Uninstall) {
    Say 'Uninstalling mic-monitor...'
    Stop-MicMonitor
    Remove-Owned $Venv
    Remove-Owned $Bin
    Remove-Owned $Icon
    foreach ($s in $Shortcut, $OldShortcut) {
        if (Test-Path -LiteralPath $s) { Remove-Item -LiteralPath $s -Force }
    }
    # The tray's Start at login entry (mic_monitor.autostart).
    foreach ($key in 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run',
                     'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run') {
        Remove-ItemProperty -LiteralPath $key -Name 'mic-monitor' -ErrorAction SilentlyContinue
    }
    Remove-FromUserPath
    Say ''
    Say 'mic-monitor is uninstalled.'
    Say ''
    Say 'Kept, delete them if you want a clean slate:'
    Say "  settings   $env:APPDATA\mic-monitor"
    Say "  logs       $Root"
    return
}

# ------------------------------------------------------------------ install

Say 'Installing mic-monitor...'
Say ''

$python = Find-Python
if (-not $python) { $python = Install-Python }
Say "Python:   $python"

Stop-MicMonitor

Say "Creating: $Venv"
Remove-Owned $Venv
& $python -m venv $Venv
if ($LASTEXITCODE -ne 0) { throw "could not create a virtual environment with $python (exit code $LASTEXITCODE)." }
$venvPython = Join-Path $Venv 'Scripts\python.exe'

Say "Package:  $Source"
& $venvPython -m pip install --quiet --disable-pip-version-check $Source
if ($LASTEXITCODE -ne 0) { throw "pip could not install mic-monitor from $Source (exit code $LASTEXITCODE)." }

# The exes pip generates carry the venv's python path inside them, so copies
# keep working from anywhere. A separate bin folder keeps the venv's own
# python.exe and pip off your PATH.
Remove-Owned $Bin
New-Item -ItemType Directory -Path $Bin -Force | Out-Null
foreach ($app in $Apps) {
    $src = Join-Path $Venv "Scripts\$app"
    if (-not (Test-Path -LiteralPath $src)) { throw "expected $src after install, but it is missing." }
    Copy-Item -LiteralPath $src -Destination (Join-Path $Bin $app) -Force
}

# Tray icon image for the shortcut; the exe would otherwise show the Python icon.
try {
    & $venvPython -c "from mic_monitor.tray import make_icon; make_icon(True).save(r'$Icon')" 2>&1 | Out-Null
} catch { }

if (Test-Path -LiteralPath $OldShortcut) { Remove-Item -LiteralPath $OldShortcut -Force }
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($Shortcut)
$link.TargetPath = Join-Path $Bin 'mic-monitor-tray.exe'
$link.WorkingDirectory = $Root
$link.Description = 'mic-monitor tray icon: hear your microphone in your headphones'
if (Test-Path -LiteralPath $Icon) { $link.IconLocation = $Icon }
$link.Save()

$pathAdded = Add-ToUserPath

$installed = & (Join-Path $Bin 'mic-monitor.exe') --version 2>&1
if ($LASTEXITCODE -ne 0) { throw "the installed mic-monitor command does not run: $installed" }

if (-not $NoLaunch) {
    Start-Process -FilePath (Join-Path $Bin 'mic-monitor-tray.exe') -WorkingDirectory $Root
}

Say ''
Say "$installed is installed."
Say ''
Say 'Tray icon:'
if ($NoLaunch) { Say '  in the Start Menu as "Mic Monitor"' }
else { Say '  running now (green = on, grey = off, click to toggle)' ; Say '  also in the Start Menu as "Mic Monitor"' }
Say ''
Say 'Commands (open a new terminal first):'
Say '  mic-monitor            toggle monitoring on or off'
Say '  mic-monitor list       show your audio devices'
Say '  mic-monitor config --in <mic> --out <headphones>'
Say '                         pick devices by part of their name'
if ($pathAdded) {
    Say ''
    Say "Added to your PATH: $Bin"
}
Say ''
Say 'Uninstall:'
Say "  & ([scriptblock]::Create((irm $SelfUrl))) -Uninstall"
