<#
.SYNOPSIS
    Run tests/live_test_sftp_connect_v2_5.py against a throwaway Uptime Kuma
    container and a throwaway SFTP server, then destroy both.
.DESCRIPTION
    Wraps run_disposable_kuma.ps1, which only knows how to start one container.
    This starts a disposable SFTP server (linuxserver/openssh-server) on the same
    Docker host first, generates a per-run password and two ed25519 key pairs
    (one passphrase-protected), hands the details to the test through the
    process environment, and calls the runner. Both containers share the default
    bridge network, so Uptime Kuma reaches the SFTP server by its bridge address
    and nothing about the SFTP server is published on the host.

    Everything is removed in a finally block: the SFTP container, the local key
    files, and the credential environment variables. The Docker host address and
    SSH user are never printed (same sanitizer as the runner).
.PARAMETER KumaImage
    Uptime Kuma image. Defaults to louislam/uptime-kuma:2.5.4 (the SFTP floor).
.PARAMETER Port
    Host port for the Kuma container. 3001 is refused by the runner.
.PARAMETER SftpImage
    SFTP server image.
.EXAMPLE
    pwsh -File scripts/run_disposable_kuma_sftp.ps1 -Port 3026
.EXAMPLE
    pwsh -File scripts/run_disposable_kuma_sftp.ps1 `
        -KumaImage louislam/uptime-kuma:2.5.12 -Port 3027
.NOTES
    Requires DOCKER-HOST and DOCKER-USER in the root .env, key-based SSH to that
    host, Docker on it, and ssh-keygen locally. Output is ASCII only.
    The generated credentials land in the SFTP container's environment and are
    visible in `docker inspect` while it runs. They are random per run and the
    container is destroyed at the end; do not reuse this pattern for anything
    that matters.
#>
[CmdletBinding()]
param(
    [string]$KumaImage = 'louislam/uptime-kuma:2.5.4',
    [int]$Port = 3026,
    [string]$SftpImage = 'linuxserver/openssh-server',
    [string]$Python = '.venv\Scripts\python.exe',
    # Leave the Kuma container running (the SFTP container and keys are still
    # removed), so its logs can be read. Remove it by hand afterwards.
    [switch]$KeepKuma
)
$ErrorActionPreference = 'Continue'

if (-not (Test-Path '.env')) {
    Write-Output "FAIL: no .env at the repository root (needs DOCKER-HOST, DOCKER-USER)."
    exit 1
}
$vals = @{}
Get-Content '.env' | Where-Object { $_ -match '=' } | ForEach-Object {
    $parts = $_ -split '=', 2
    $vals[$parts[0].Trim()] = $parts[1].Trim()
}
$dockerHost = $vals['DOCKER-HOST']
$dockerUser = $vals['DOCKER-USER']
if (-not $dockerHost -or -not $dockerUser) {
    Write-Output "FAIL: DOCKER-HOST and/or DOCKER-USER missing from .env"
    exit 1
}
if (-not (Get-Command ssh-keygen -ErrorAction SilentlyContinue)) {
    Write-Output "FAIL: ssh-keygen not found on PATH."
    exit 1
}
$sshTarget = "$dockerUser@$dockerHost"
$sftpName = "sftp-disposable-$Port"
$sftpUser = 'tester'
$sftpPort = 2222

function Hide-Target([string]$text) {
    if (-not $text) { return $text }
    $text = $text -replace [regex]::Escape($dockerHost), '<docker-host>'
    $text = $text -replace [regex]::Escape($dockerUser), '<user>'
    return $text
}
function Write-Clean($lines) {
    foreach ($line in $lines) { Write-Output (Hide-Target ([string]$line)) }
}
function Invoke-Remote([string]$command) {
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new `
        $sshTarget $command 2>&1
}
function Quote-Remote([string]$value) {
    return "'" + ($value -replace "'", "'\''") + "'"
}
function New-RandomString([int]$length) {
    $chars = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'.ToCharArray()
    return -join (1..$length | ForEach-Object { $chars | Get-Random })
}

$keyDir = Join-Path (Get-Location) ".temp\sftp-live-$Port"
$sftpPassword = New-RandomString 20
$passphrase = New-RandomString 16
$exitCode = 1

try {
    Write-Output "== generating throwaway keys =="
    New-Item -ItemType Directory -Force $keyDir | Out-Null
    $k1 = Join-Path $keyDir 'k1'
    $k2 = Join-Path $keyDir 'k2'
    foreach ($f in $k1, "$k1.pub", $k2, "$k2.pub") { Remove-Item $f -ErrorAction SilentlyContinue }
    & ssh-keygen -q -t ed25519 -N "" -C throwaway-1 -f $k1 | Out-Null
    & ssh-keygen -q -t ed25519 -N $passphrase -C throwaway-2 -f $k2 | Out-Null
    if (-not ((Test-Path $k1) -and (Test-Path $k2))) {
        Write-Output "FAIL: ssh-keygen did not produce the key files."
        exit 1
    }
    $pub1 = (Get-Content "$k1.pub" -Raw).Trim()
    $pub2 = (Get-Content "$k2.pub" -Raw).Trim()

    Write-Output ""
    Write-Output "== starting SFTP server ($SftpImage) =="
    $run = "docker rm -f $sftpName 2>/dev/null; docker run -d --name $sftpName" +
        " -e PUID=1000 -e PGID=1000 -e TZ=Etc/UTC" +
        " -e USER_NAME=$sftpUser" +
        " -e " + (Quote-Remote "USER_PASSWORD=$sftpPassword") +
        " -e PASSWORD_ACCESS=true -e LOG_STDOUT=true" +
        " -e " + (Quote-Remote "PUBLIC_KEY=$pub1") +
        " $SftpImage"
    Write-Clean (Invoke-Remote $run | Select-Object -Last 3)
    if ($LASTEXITCODE -ne 0) {
        Write-Output "FAIL: could not start the SFTP container."
        exit 1
    }

    $ready = $false
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        $logs = (Invoke-Remote "docker logs $sftpName 2>&1" | Out-String)
        if ($logs -match 'Server listening') { $ready = $true; break }
        Start-Sleep -Seconds 3
    }
    if (-not $ready) {
        Write-Output "FAIL: SFTP server did not start listening within 90s."
        exit 1
    }
    Write-Output "  PASS sshd listening"

    # Second (passphrase-protected) key, appended to the same user.
    Write-Clean (Invoke-Remote ("docker exec $sftpName sh -c " +
        (Quote-Remote "echo $(Quote-Remote $pub2) >> /config/.ssh/authorized_keys")))
    $ip = (Invoke-Remote "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' $sftpName" |
        Out-String).Trim()
    if ($ip -notmatch '^\d+\.\d+\.\d+\.\d+$') {
        Write-Output "FAIL: could not read the SFTP container's bridge address."
        exit 1
    }

    $env:SFTP_TEST_HOST = $ip
    $env:SFTP_TEST_PORT = "$sftpPort"
    $env:SFTP_TEST_USER = $sftpUser
    $env:SFTP_TEST_PASSWORD = $sftpPassword
    $env:SFTP_TEST_KEY_FILE = $k1
    $env:SFTP_TEST_KEY_PP_FILE = $k2
    $env:SFTP_TEST_PASSPHRASE = $passphrase

    Write-Output ""
    $extra = @()
    if ($KeepKuma) { $extra += '-KeepContainer' }
    & pwsh -File scripts/run_disposable_kuma.ps1 `
        -Script tests/live_test_sftp_connect_v2_5.py `
        -Image $KumaImage -Port $Port -Python $Python `
        -DockerEnv UPTIME_KUMA_DB_TYPE=sqlite @extra
    $exitCode = $LASTEXITCODE
}
finally {
    foreach ($n in 'HOST', 'PORT', 'USER', 'PASSWORD', 'KEY_FILE', 'KEY_PP_FILE', 'PASSPHRASE') {
        Remove-Item "Env:SFTP_TEST_$n" -ErrorAction SilentlyContinue
    }
    Write-Output ""
    Write-Output "== removing $sftpName and local keys =="
    Write-Clean (Invoke-Remote "docker rm -f $sftpName")
    Remove-Item $keyDir -Recurse -Force -ErrorAction SilentlyContinue
}
exit $exitCode
