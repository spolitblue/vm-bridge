# run_bridge.ps1 — keep the host<->Kali bridge alive.
#
#   1. starts host_bridge.py (127.0.0.1:8899) if it is not already listening
#   2. holds an SSH reverse tunnel open:  VM 127.0.0.1:8899 -> host 127.0.0.1:8899
#   3. re-detects the VM IP with vmrun when the old one stops answering
#
# No administrator rights are needed: the VM talks to its own loopback, and the
# Windows firewall never sees an inbound packet (the host dials out).
#
# Launch it through start-bridge.vbs (hidden) or by hand:
#   powershell -NoProfile -ExecutionPolicy Bypass -File run_bridge.ps1

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$logFile = Join-Path $here 'bridge.log'
$sshLog = Join-Path $here 'ssh.log'
$tokenFile = Join-Path $here 'token.txt'
$bridgePort = 8899
$vmUser = 'kali'
$defaultVmIp = '192.168.178.128'
$vmx = 'C:\Users\LZY20\Desktop\harness\kali-vm\kali-linux-2026.2-vmware-amd64.vmwarevm\kali-linux-2026.2-vmware-amd64.vmx'
$vmrun = 'C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe'

function Write-Log([string]$message) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message
    try { [IO.File]::AppendAllText($logFile, $line + "`r`n", [Text.Encoding]::UTF8) } catch { }
}

function Test-Port([int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $port)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Resolve-Python {
    $candidates = @(
        'C:\Users\LZY20\AppData\Local\Programs\Python\Python312\python.exe',
        (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    return $null
}

function Start-Executor {
    $python = Resolve-Python
    if (-not $python) {
        Write-Log 'FATAL: python.exe not found; cannot start host_bridge.py'
        return $false
    }
    Start-Process -FilePath $python -ArgumentList ('"{0}"' -f (Join-Path $here 'host_bridge.py')) -WindowStyle Hidden
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 300
        if (Test-Port $bridgePort) {
            Write-Log "executor up on 127.0.0.1:$bridgePort"
            return $true
        }
    }
    Write-Log 'FATAL: host_bridge.py did not start listening'
    return $false
}

function Get-VmIp {
    if ((Test-Path $vmrun) -and (Test-Path $vmx)) {
        try {
            $ip = & $vmrun -T ws getGuestIPAddress $vmx 2>$null
            if ($ip -match '^\d+\.\d+\.\d+\.\d+$') { return $ip.Trim() }
        } catch { }
    }
    return $null
}

# ---- single instance: a session-local named mutex, released when this process dies
$mutex = New-Object System.Threading.Mutex($false, 'Local\DSHHostBridgeKeeper')
try {
    if (-not $mutex.WaitOne(0, $false)) {
        Write-Log 'keeper already running in this session; exiting'
        exit 0
    }
} catch {
    Write-Log "mutex unavailable ($($_.Exception.Message)); continuing without the guard"
}

# ---- token ---------------------------------------------------------------
if (-not (Test-Path $tokenFile)) {
    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    Set-Content -Path $tokenFile -Value (($bytes | ForEach-Object { $_.ToString('x2') }) -join '') -Encoding ascii
    Write-Log 'generated a new bridge token'
}

Write-Log "keeper starting (pid $PID)"

# ---- main loop -----------------------------------------------------------
$vmIp = $defaultVmIp
$delay = 5
while ($true) {
    if (-not (Test-Port $bridgePort)) {
        [void](Start-Executor)
    }

    $started = Get-Date
    Write-Log "tunnel -> ${vmUser}@${vmIp}:${bridgePort}"
    $sshArgs = 'ssh -N -R {0}:127.0.0.1:{0} -o ExitOnForwardFailure=yes -o BatchMode=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 {1}@{2} 2>>"{3}"' -f $bridgePort, $vmUser, $vmIp, $sshLog
    & cmd.exe /c $sshArgs
    $rc = $LASTEXITCODE
    $lived = ((Get-Date) - $started).TotalSeconds

    if ($lived -ge 30) { $delay = 5 } else { $delay = [Math]::Min($delay * 2, 60) }
    Write-Log "tunnel dropped after $([int]$lived)s (ssh exit $rc); retry in ${delay}s"

    Start-Sleep -Seconds $delay
    $detected = Get-VmIp
    if ($detected -and $detected -ne $vmIp) {
        Write-Log "VM IP changed: $vmIp -> $detected"
        $vmIp = $detected
        $delay = 5
    }
}
