# 一键构建并签名 APK（无需 Android Studio）
# 用法：右键“使用 PowerShell 运行”，或在终端执行 .\build_apk.ps1

$ErrorActionPreference = "Stop"
$root = "d:\文件传输"
$app = "$root\android-app"

$env:JAVA_HOME = "$root\jdk17\jdk-17.0.20.1+1"
$env:ANDROID_HOME = "$root\android-sdk"
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"

Write-Host "[1/2] 编译 APK ..." -ForegroundColor Cyan
& "$root\gradle\gradle-8.5\bin\gradle.bat" assembleDebug --no-daemon -p $app
if ($LASTEXITCODE -ne 0) { throw "编译失败" }

Write-Host "[2/2] 使用项目密钥签名 ..." -ForegroundColor Cyan

# 从已有凭据文件读取，避免在任何命令/脚本中出现明文密码
$credLines = Get-Content "$app\keystore-credentials.txt" -Encoding UTF8
$storepass = (($credLines | Where-Object { $_ -match '^storepass:' }) -replace '^storepass:\s*', '').Trim()
$keypass   = (($credLines | Where-Object { $_ -match '^keypass:' })   -replace '^keypass:\s*', '').Trim()
$alias     = (($credLines | Where-Object { $_ -match '^alias:' })     -replace '^alias:\s*', '').Trim()

$src = "$app\app\build\outputs\apk\debug\app-debug.apk"
$out = "$app\lan-transfer.apk"
if (Test-Path $out) { Remove-Item $out -Force }

& "$env:ANDROID_HOME\build-tools\34.0.0\apksigner.bat" sign `
    --ks "$app\lan-transfer.keystore" `
    --ks-key-alias $alias `
    --ks-pass "pass:$storepass" `
    --key-pass "pass:$keypass" `
    --out $out `
    $src
if ($LASTEXITCODE -ne 0) { throw "签名失败" }

& "$env:ANDROID_HOME\build-tools\34.0.0\apksigner.bat" verify --print-certs $out | Select-Object -First 2

Write-Host "`n构建完成: $out" -ForegroundColor Green
Get-ChildItem $out | Select-Object Name, Length, LastWriteTime
