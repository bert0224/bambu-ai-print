[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,

    [Parameter(Mandatory = $true)]
    [string]$VenvPath
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python 可执行文件不存在：$PythonExe"
}

$resolvedVenv = [System.IO.Path]::GetFullPath($VenvPath)
& $PythonExe -m venv $resolvedVenv
if ($LASTEXITCODE -ne 0) {
    throw "创建虚拟环境失败，退出码：$LASTEXITCODE"
}

$venvPython = Join-Path $resolvedVenv 'Scripts\python.exe'
& $venvPython -m pip install --upgrade pip 'cadquery==2.7.0' 'nlopt==2.9.1'
if ($LASTEXITCODE -ne 0) {
    throw "安装 CadQuery 环境失败，退出码：$LASTEXITCODE"
}

& $venvPython -c "import cadquery, nlopt, casadi; print(f'CadQuery={cadquery.__version__}, nlopt={nlopt.__version__}, casadi={casadi.__version__}')"
if ($LASTEXITCODE -ne 0) {
    throw "CadQuery 依赖组合检查失败，退出码：$LASTEXITCODE"
}

Write-Output "CadQuery 隔离环境可用：$resolvedVenv"
