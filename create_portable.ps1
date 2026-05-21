# create_portable.ps1 - 创建绿色版压缩包（无需NSIS）

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  AI智能助手 - 绿色版打包工具" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查必要文件是否存在
$requiredFiles = @(
    "dist\ai_assistant.exe",
    "dist\start_ai.bat",
    "dist\run_backend\run_backend.exe"
)

$allExist = $true
foreach ($file in $requiredFiles) {
    if (-not (Test-Path $file)) {
        Write-Host "❌ 缺少文件: $file" -ForegroundColor Red
        $allExist = $false
    }
}

if (-not $allExist) {
    Write-Host ""
    Write-Host "请先完成打包步骤再运行此脚本" -ForegroundColor Yellow
    exit 1
}

# 创建压缩包
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$zipName = "AI智能助手_绿色版_$timestamp.zip"

Write-Host "📦 正在创建压缩包: $zipName" -ForegroundColor Green
Compress-Archive -Path "dist\*" -DestinationPath $zipName -Force

Write-Host ""
Write-Host "✅ 打包完成！" -ForegroundColor Green
Write-Host "📍 文件位置: $(Get-Location)\$zipName" -ForegroundColor Cyan
Write-Host ""
Write-Host "使用说明：" -ForegroundColor Yellow
Write-Host "1. 解压到任意目录" -ForegroundColor White
Write-Host "2. 编辑 run_backend\.env 配置API密钥" -ForegroundColor White
Write-Host "3. 双击 start_ai.bat 启动应用" -ForegroundColor White
