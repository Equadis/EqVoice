param(
    [string]$JoinCodeFile = "D:\Octowow\.eco-work\eqvoice-secrets\join-code.txt",
    [string]$HubUrl = "wss://eqvoice-signalling-prod.eqvoice.workers.dev/ws",
    [switch]$Publish
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
function Assert-Exit([string]$Step) { if ($LASTEXITCODE -ne 0) { throw "$Step failed ($LASTEXITCODE)" } }
$version = "0.7.4"
$nativeBuild = Join-Path $PWD "build\native-v074"
cmake -S classicapi-extension -B "$nativeBuild" -A Win32
Assert-Exit "CMake configure"
cmake --build "$nativeBuild" --config Release
Assert-Exit "EqVoice DLL build"
$env:EQVOICE_NATIVE_DLL = Join-Path $nativeBuild "Release\EqVoice.dll"
if (!(Test-Path $env:EQVOICE_NATIVE_DLL)) { throw "EqVoice.dll missing" }
$upstreamDir = Join-Path $PWD "build\official-classicapi"
New-Item -ItemType Directory -Path $upstreamDir -Force | Out-Null
$env:EQVOICE_CLASSICAPI_DLL = Join-Path $upstreamDir "ClassicAPI.dll"
Invoke-WebRequest -Uri "https://github.com/brues-code/ClassicAPI/releases/download/v1.21.1/ClassicAPI.dll" -OutFile $env:EQVOICE_CLASSICAPI_DLL
if ((Get-FileHash $env:EQVOICE_CLASSICAPI_DLL -Algorithm SHA256).Hash.ToLowerInvariant() -ne "a808dbd3f1aa6bb6a6c7022b324a83fe75110332c662bf5e9b115386895f618a") { throw "Official ClassicAPI checksum mismatch" }
$joinCode = (Get-Content $JoinCodeFile -TotalCount 1).Trim()
if (!$joinCode) { throw "Voice hub join code missing" }
python tools/build_installer.py --hub-url "$HubUrl" --join-code "$joinCode"
Assert-Exit "Installer build"
Copy-Item "dist\EqVoice-Test-Setup-$version.exe" "dist\EqVoice-Setup.exe" -Force
Get-FileHash "dist\EqVoice-Setup.exe" -Algorithm SHA256
if ($Publish) {
    if ((Read-Host "Confirm Windows and two-player in-game checks passed. Type PUBLISH") -ne "PUBLISH") { throw "Publication cancelled" }
    $commit = (git rev-parse HEAD).Trim()
    Assert-Exit "Resolve release commit"
    gh release create "v$version" "dist\EqVoice-Setup.exe" --target "$commit" --prerelease --title "EqVoice v$version" --notes "Experimental native overhead icons; independent EqVoice.dll and official ClassicAPI dependency."
    Assert-Exit "Publish release"
}
