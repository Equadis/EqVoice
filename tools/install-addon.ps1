<#
.SYNOPSIS
  Installs (or links, or removes) the EqVoice addon in a WoW AddOns folder.

.EXAMPLE
  .\tools\install-addon.ps1 -AddOns "D:\Octowow\Interface\AddOns"
  .\tools\install-addon.ps1 -AddOns "D:\Octowow\Interface\AddOns" -Link     # junction: edits show up after /reload
  .\tools\install-addon.ps1 -AddOns "D:\Octowow\Interface\AddOns" -Remove
#>
param(
  [Parameter(Mandatory = $true)][string]$AddOns,
  [switch]$Link,
  [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$src = Join-Path $PSScriptRoot '..\eqvoice-addon\EqVoice' | Resolve-Path
$dst = Join-Path $AddOns 'EqVoice'
if (-not (Test-Path $AddOns)) { throw "AddOns folder not found: $AddOns" }

if (Test-Path $dst) {
  $item = Get-Item $dst -Force
  if ($item.LinkType) { Remove-Item $dst -Force }        # a junction: remove the link only, never the target
  else { Remove-Item $dst -Recurse -Force }
  Write-Host "removed existing $dst"
}
# this addon's former name: it must not stay beside the new one (two addons would both answer /eqvc)
$old = Join-Path $AddOns 'OctoVoice'
if (Test-Path (Join-Path $old 'OctoVoice.toc')) { Remove-Item $old -Recurse -Force; Write-Host "removed the old addon $old" }
if ($Remove) { return }

if ($Link) {
  New-Item -ItemType Junction -Path $dst -Target $src | Out-Null
  Write-Host "linked $dst -> $src"
} else {
  Copy-Item $src $dst -Recurse
  Write-Host "copied to $dst"
}
Write-Host "In game: /reload, then /ev status and /ev diag api"
