<#
    打包「校园网一键联网」为单文件 exe。
    用法：  pwsh -File build.ps1
            pwsh -File build.ps1 -SkipTests        # 跳过 pytest
            pwsh -File build.ps1 -Onedir           # 打包成文件夹版（启动更快）
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipInstall,
    [switch]$Onedir
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
$uv = Join-Path $root '.venv\Scripts\uv.exe'
$dist = Join-Path $root 'dist'
$work = Join-Path $root 'build\pyinstaller'
$assets = Join-Path $root 'assets'

if (-not (Test-Path $py)) { throw "找不到虚拟环境解释器：$py" }

if (-not $SkipInstall) {
    Write-Host '== 0/4 依赖对齐（uv）==' -ForegroundColor Cyan
    $env:UV_CACHE_DIR = Join-Path $root '.uv-cache'
    & $uv pip install --python $py -i https://pypi.tuna.tsinghua.edu.cn/simple -r (Join-Path $root 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
}

if (-not $SkipTests) {
    Write-Host '== 1/4 离线自检 ==' -ForegroundColor Cyan
    & $py -m pytest -q (Join-Path $root 'tests')
    if ($LASTEXITCODE -ne 0) { throw '自检失败，先修好再打包' }
}

Write-Host '== 2/4 生成图标/立绘 ==' -ForegroundColor Cyan
& $py (Join-Path $root 'tools\make_assets.py')
if ($LASTEXITCODE -ne 0) { throw '素材生成失败' }

Write-Host '== 3/4 PyInstaller 打包 ==' -ForegroundColor Cyan
$mode = if ($Onedir) { '--onedir' } else { '--onefile' }
$pyArgs = @(
    '-m', 'PyInstaller', '--noconfirm', '--clean', $mode, '--windowed',
    '--name', 'GUIT校园网一键联网',
    '--icon', (Join-Path $assets 'icon.ico'),
    '--add-data', ('{0};assets' -f $assets),
    '--collect-all', 'ddddocr',
    '--collect-all', 'onnxruntime',
    '--exclude-module', 'tkinter',
    '--exclude-module', 'PySide6.QtWebEngineCore',
    '--exclude-module', 'PySide6.QtWebEngineWidgets',
    '--exclude-module', 'PySide6.QtQml',
    '--exclude-module', 'PySide6.QtQuick',
    '--exclude-module', 'PySide6.Qt3DCore',
    '--exclude-module', 'matplotlib',
    '--distpath', $dist,
    '--workpath', $work,
    '--specpath', $work,
    (Join-Path $root 'run.py')
)
& $py @pyArgs
if ($LASTEXITCODE -ne 0) { throw '打包失败' }

Write-Host '== 4/4 产物级验证 ==' -ForegroundColor Cyan
& $py (Join-Path $root 'tools\verify_exe.py')
if ($LASTEXITCODE -ne 0) { throw '产物验证失败' }

$exe = Join-Path $dist 'GUIT校园网一键联网.exe'
Write-Host ''
Write-Host ('打包完成喵~ ' + $exe) -ForegroundColor Green
Write-Host ('大小：' + [math]::Round((Get-Item $exe).Length / 1MB, 1) + ' MB')
