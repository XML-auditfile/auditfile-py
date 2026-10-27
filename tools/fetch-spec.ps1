<#
.SYNOPSIS
    Copies generated/ of the auditfile-spec commit pinned in spec.lock.json into spec/generated/.

.DESCRIPTION
    spec/ is not committed; run this after cloning and whenever spec.lock.json changes.
    Only generated/ is copied: the reader needs nothing else, and spec/ is a plain folder, not a git checkout.
    The commit is fetched into a temporary folder that is removed afterwards.
    When the lock names a tag, the tag must point to the pinned commit.
    Works in Windows PowerShell 5.1 and PowerShell 7; needs git.

.EXAMPLE
    pwsh tools/fetch-spec.ps1
#>
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

function Invoke-Git {
    $output = & git @args
    if ($LASTEXITCODE -ne 0) {
        throw "git $($args -join ' ') failed with exit code $LASTEXITCODE."
    }

    return $output
}

$lock = Get-Content (Join-Path $Root 'spec.lock.json') -Raw | ConvertFrom-Json
if ($lock.commit -notmatch '^[0-9a-f]{40}$') {
    throw "spec.lock.json: 'commit' must be a full 40-character commit SHA."
}

$url = "https://github.com/$($lock.repository).git"
$label = if ($lock.tag) { "$($lock.tag) ($($lock.commit.Substring(0, 7)))" } else { "commit $($lock.commit.Substring(0, 7)) (no release tag)" }
$target = Join-Path $Root 'spec'
$stamp = Join-Path $target 'commit.txt'

if ((Test-Path $stamp) -and (Test-Path (Join-Path $target 'generated/registry.json')) -and ((Get-Content $stamp -Raw).Trim() -eq $lock.commit)) {
    Write-Host "auditfile-spec $label is already in spec/generated/."
    return
}

if ($lock.tag) {
    # A peeled entry (^{}) exists for annotated tags and holds the commit; lightweight tags only have the plain entry.
    $refs = Invoke-Git ls-remote $url "refs/tags/$($lock.tag)" "refs/tags/$($lock.tag)^{}"
    $commits = @($refs | ForEach-Object { ($_ -split '\s+')[0] })
    if ($commits.Count -eq 0) {
        throw "Tag '$($lock.tag)' does not exist in $($lock.repository)."
    }

    if ($commits[-1] -ne $lock.commit) {
        throw "Tag '$($lock.tag)' points to $($commits[-1]), but spec.lock.json pins $($lock.commit)."
    }
}

$temp = Join-Path ([System.IO.Path]::GetTempPath()) ("auditfile-spec-" + [System.Guid]::NewGuid().ToString('N'))
try {
    Invoke-Git init --quiet $temp | Out-Null
    Invoke-Git -C $temp remote add origin $url | Out-Null
    Invoke-Git -C $temp fetch --quiet --depth 1 origin $lock.commit | Out-Null
    # Only generated/ is checked out; the rest of the spec never reaches the disk.
    Invoke-Git -C $temp -c advice.detachedHead=false checkout --quiet FETCH_HEAD -- generated | Out-Null

    if (Test-Path $target) {
        Remove-Item $target -Recurse -Force
    }

    New-Item -ItemType Directory -Path $target | Out-Null
    Copy-Item (Join-Path $temp 'generated') -Destination $target -Recurse
    Set-Content -Path $stamp -Value $lock.commit -Encoding ascii
}
finally {
    if (Test-Path $temp) {
        Remove-Item $temp -Recurse -Force
    }
}

Write-Host "Copied generated/ of auditfile-spec $label into spec/generated/."
