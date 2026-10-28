# Semantic Versioning 2.0.0 helpers, shared by the workflows. Dot-source: . ./tools/semver.ps1

$SemVerPattern = '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)' +
    '(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?' +
    '(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$'

# True when the text is a semantic version without prefix, e.g. 1.0.1 or 1.1.0-beta.1.
function Test-SemVer([string]$Version) {
    return $Version -match $SemVerPattern
}

# The release line, major.minor, e.g. "0.1" for 0.1.3.
function Get-SemVerLine([string]$Version) {
    $m = [regex]::Match($Version, $SemVerPattern)
    if (-not $m.Success) { throw "'$Version' is not a semantic version." }
    return "$($m.Groups[1].Value).$($m.Groups[2].Value)"
}

# -1, 0 or 1. Precedence as in semver.org section 11; build metadata is ignored.
function Compare-SemVer([string]$A, [string]$B) {
    $x = [regex]::Match($A, $SemVerPattern)
    $y = [regex]::Match($B, $SemVerPattern)
    if (-not $x.Success) { throw "'$A' is not a semantic version." }
    if (-not $y.Success) { throw "'$B' is not a semantic version." }

    foreach ($i in 1..3) {
        $c = ([decimal]$x.Groups[$i].Value).CompareTo([decimal]$y.Groups[$i].Value)
        if ($c -ne 0) { return [Math]::Sign($c) }
    }

    $px = $x.Groups[4].Value
    $py = $y.Groups[4].Value
    if ($px -eq $py) { return 0 }
    if ($px -eq '') { return 1 }   # a release is higher than its pre-releases
    if ($py -eq '') { return -1 }

    $ix = $px.Split('.')
    $iy = $py.Split('.')
    for ($i = 0; $i -lt [Math]::Min($ix.Length, $iy.Length); $i++) {
        $nx = $ix[$i] -match '^\d+$'
        $ny = $iy[$i] -match '^\d+$'
        if ($nx -and $ny) { $c = ([decimal]$ix[$i]).CompareTo([decimal]$iy[$i]) }
        elseif ($nx) { $c = -1 }   # numeric identifiers are lower than alphanumeric ones
        elseif ($ny) { $c = 1 }
        else { $c = [string]::CompareOrdinal($ix[$i], $iy[$i]) }
        if ($c -ne 0) { return [Math]::Sign($c) }
    }

    return [Math]::Sign($ix.Length.CompareTo($iy.Length))
}

# The highest semantic version in the list, or $null. Entries that are not semantic versions are skipped.
function Get-HighestSemVer([string[]]$Versions) {
    $highest = $null
    foreach ($version in $Versions) {
        if ((Test-SemVer $version) -and ($null -eq $highest -or (Compare-SemVer $version $highest) -gt 0)) {
            $highest = $version
        }
    }

    return $highest
}

# Checks a version to release against the existing tags and the branch it is released from:
# - main: higher than every existing release;
# - release/X.Y: in line X.Y and higher than the latest release in that line (maintenance of an older line).
# Throws when the version may not be released. Returns $true when it becomes GitHub's latest release.
function Assert-ReleaseVersion([string]$Version, [string]$Ref, [string[]]$Tags) {
    if (-not (Test-SemVer $Version)) {
        throw "'$Version' is not a semantic version without prefix (e.g. 1.0.1 or 1.1.0-beta.1)."
    }

    $releases = @($Tags | Where-Object { Test-SemVer $_ })
    $line = Get-SemVerLine $Version
    $highest = Get-HighestSemVer $releases

    if ($Ref -eq 'refs/heads/main') {
        $previous = $highest
        $scope = 'every existing release'
    }
    elseif ($Ref -eq "refs/heads/release/$line") {
        $previous = Get-HighestSemVer @($releases | Where-Object { (Get-SemVerLine $_) -eq $line })
        $scope = "the $line.x releases"
    }
    else {
        throw "$Version is released from main, or from release/$line for maintenance of an older line (this run: $Ref)."
    }

    if ($previous -and (Compare-SemVer $Version $previous) -le 0) {
        throw "$Version must be higher than $previous, the latest of $scope."
    }

    # GitHub's "latest release" is the highest stable one; pre-releases never are.
    $highestStable = Get-HighestSemVer @($releases | Where-Object { $_ -notmatch '^[^+]*-' })
    $isLatest = ($Version -notmatch '^[^+]*-') -and ((-not $highestStable) -or (Compare-SemVer $Version $highestStable) -gt 0)
    Write-Host "Releasing $Version (previous: $(if ($previous) { $previous } else { 'none' }); latest release: $isLatest)."
    return $isLatest
}
