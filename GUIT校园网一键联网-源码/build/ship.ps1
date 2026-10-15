$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$py = 'D:\DSH\campus-login\.venv\Scripts\python.exe'
$root = 'D:\DSH\campus-login'
$desk = 'D:\Desktop\GUIT校园网一键联网'
Set-Location $root
$log = Join-Path $root 'build\ship.log'
"=== ship started $(Get-Date -Format o) ===" | Out-File $log -Encoding utf8

# 0) 关掉占用（自己的测试进程 + 桌面上正开着的实例）
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.ExecutablePath -like '*campus-login*' -or $_.ExecutablePath -like '*校园网一键联网*' } |
  ForEach-Object {
    "closing $($_.ProcessId) $($_.Name)" | Out-File $log -Append -Encoding utf8
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
Start-Sleep -Seconds 3

Remove-Item (Join-Path $root 'dist') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $root 'dist-onedir\GUIT校园网一键联网') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $root 'dist-onedir\GUIT校园网一键联网-解压即用.zip') -Force -ErrorAction SilentlyContinue

Write-Output '--- sync version ---' | Out-File $log -Append -Encoding utf8
& $py (Join-Path $root 'tools\make_version.py') *>> $log

$common = @(
  '-m','PyInstaller','--noconfirm','--clean',
  '--name','GUIT校园网一键联网',
  '--icon', (Join-Path $root 'assets\icon.ico'),
  '--version-file', (Join-Path $root 'build\version_info.txt'),
  '--add-data', ((Join-Path $root 'assets') + ';assets'),
  '--collect-all','ddddocr',
  '--collect-all','onnxruntime',
  '--exclude-module','tkinter',
  '--exclude-module','PySide6.QtWebEngineCore',
  '--exclude-module','PySide6.QtWebEngineWidgets',
  '--exclude-module','PySide6.QtQml',
  '--exclude-module','PySide6.QtQuick',
  (Join-Path $root 'run.py')
)

Write-Output '--- onefile ---' | Out-File $log -Append -Encoding utf8
& $py @common '--onefile' '--windowed' '--distpath' (Join-Path $root 'dist') '--workpath' (Join-Path $root 'build\pi-of') '--specpath' (Join-Path $root 'build\pi-of') *>> $log
Write-Output ("onefile exit=" + $LASTEXITCODE) | Out-File $log -Append -Encoding utf8

Write-Output '--- onedir ---' | Out-File $log -Append -Encoding utf8
& $py @common '--onedir' '--windowed' '--distpath' (Join-Path $root 'dist-onedir') '--workpath' (Join-Path $root 'build\pi-od') '--specpath' (Join-Path $root 'build\pi-od') *>> $log
Write-Output ("onedir exit=" + $LASTEXITCODE) | Out-File $log -Append -Encoding utf8

Write-Output '--- verify (before touching desktop) ---' | Out-File $log -Append -Encoding utf8
$env:TEMP = Join-Path $root 'build\vtemp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
& $py (Join-Path $root 'tools\verify_exe.py') *>> $log
$ver = $LASTEXITCODE
Write-Output ("verify exit=" + $ver) | Out-File $log -Append -Encoding utf8

if ($ver -ne 0) {
  Write-Output 'VERIFY FAILED - desktop left untouched' | Out-File $log -Append -Encoding utf8
  Write-Output 'DONE'
  exit 1
}

Write-Output '--- desktop refresh ---' | Out-File $log -Append -Encoding utf8
$bak = Join-Path $root 'build\config-backup.json'
Remove-Item $bak -Force -ErrorAction SilentlyContinue
if (Test-Path (Join-Path $desk 'config.json')) {
  Copy-Item (Join-Path $desk 'config.json') $bak -Force
  Write-Output 'kept user config.json' | Out-File $log -Append -Encoding utf8
}
Remove-Item $desk -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $root 'dist-onedir\GUIT校园网一键联网') $desk -Recurse -Force
if (Test-Path $bak) { Copy-Item $bak (Join-Path $desk 'config.json') -Force }
Copy-Item (Join-Path $root 'dist\GUIT校园网一键联网.exe') 'D:\Desktop\GUIT校园网一键联网.exe' -Force

Remove-Item (Join-Path $desk '_selftest.log') -Force -ErrorAction SilentlyContinue
$p = Start-Process -FilePath (Join-Path $desk '校园网一键联网.exe') -ArgumentList '--selftest' -PassThru -Wait
Write-Output ("desktop verify exit=" + $p.ExitCode) | Out-File $log -Append -Encoding utf8
if (Test-Path (Join-Path $desk '_selftest.log')) { Get-Content (Join-Path $desk '_selftest.log') | Out-File $log -Append -Encoding utf8 }

Write-Output 'DONE' | Out-File $log -Append -Encoding utf8
Write-Output 'DONE'