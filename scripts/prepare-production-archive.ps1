$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$token = [Guid]::NewGuid().ToString('N')
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$stageRoot = Join-Path $tempRoot "flowcheck-stage-$token"
$archivePath = Join-Path $tempRoot "flowcheck-release-$stamp-$token.zip"
$stageRootFull = [System.IO.Path]::GetFullPath($stageRoot)

function Copy-ReleaseFile([string]$relativePath) {
    $source = Join-Path $repoRoot $relativePath
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required deployment file is missing: $relativePath"
    }
    $destination = Join-Path $stageRoot $relativePath
    $destinationDirectory = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination
}

function Copy-ReleaseDirectory([string]$relativePath) {
    $source = Join-Path $repoRoot $relativePath
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw "Required deployment directory is missing: $relativePath"
    }
    $destination = Join-Path $stageRoot $relativePath
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Recurse
}

try {
    New-Item -ItemType Directory -Path $stageRoot | Out-Null

    Copy-ReleaseFile 'docker-compose.prod.yml'
    Copy-ReleaseFile 'DEPLOYMENT.md'
    Copy-ReleaseFile 'backend/Dockerfile'
    Copy-ReleaseFile 'backend/requirements.txt'
    Copy-ReleaseFile 'backend/.dockerignore'
    Get-ChildItem -LiteralPath (Join-Path $repoRoot 'backend') -File -Filter '*.py' |
        ForEach-Object { Copy-ReleaseFile (Join-Path 'backend' $_.Name) }

    foreach ($path in @(
        'frontend/Dockerfile.prod', 'frontend/package.json',
        'frontend/package-lock.json', 'frontend/index.html',
        'frontend/vite.config.js', 'frontend/.dockerignore'
    )) { Copy-ReleaseFile $path }
    Copy-ReleaseDirectory 'frontend/src'
    Copy-ReleaseDirectory 'frontend/nginx'

    foreach ($path in @(
        'demo-target/Dockerfile', 'demo-target/Dockerfile.dual-login',
        'demo-target/index.html', 'demo-target/README.md', 'demo-target/.dockerignore',
        'DUAL_LOGIN_DEMO_GUIDE.md'
    )) {
        Copy-ReleaseFile $path
    }
    Copy-ReleaseDirectory 'demo-target/dual-login'

    $forbidden = Get-ChildItem -LiteralPath $stageRoot -File -Recurse | Where-Object {
        $_.Name -match '^\.env($|\.)|\.(pem|key|db|sqlite\d*|png|jpg|jpeg|log)$' -or
        $_.FullName -match '[\\/](artifacts|data|backups|certbot|node_modules|\.git)[\\/]'
    }
    if ($forbidden) {
        throw "Unsafe deployment archive input detected: $($forbidden.Name -join ', ')"
    }

    Compress-Archive -Path (Join-Path $stageRoot '*') -DestinationPath $archivePath -CompressionLevel Optimal
    $zip = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $unsafeEntry = $zip.Entries | Where-Object {
            $_.FullName -match '(^|/)(\.env($|\.)|artifacts/|data/|backups/|certbot/|node_modules/|\.git/)' -or
            $_.FullName -match '\.(pem|key|db|sqlite\d*|png|jpg|jpeg|log)$'
        } | Select-Object -First 1
        if ($unsafeEntry) { throw "Unsafe archive member detected: $($unsafeEntry.FullName)" }
    } finally {
        $zip.Dispose()
    }

    Write-Output $archivePath
} finally {
    if ($stageRootFull.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $stageRootFull -PathType Container)) {
        Remove-Item -LiteralPath $stageRootFull -Recurse -Force
    }
}
