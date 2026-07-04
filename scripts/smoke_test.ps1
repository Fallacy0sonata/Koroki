# Koroki 2.0 - Smoke Test
# Verifies all services are up and a test request flows through the pipeline.
# Usage: .\scripts\smoke_test.ps1  (or pass custom URLs with -OrchestratorUrl etc.)

[CmdletBinding()]
param(
    [string]$OrchestratorUrl = "http://127.0.0.1:9882",
    [string]$BrainUrl        = "http://127.0.0.1:9881",
    [string]$TtsUrl          = "http://127.0.0.1:9880"
)

$ErrorCount = 0
$PassCount  = 0

function Write-Pass($msg) {
    Write-Host "[PASS] $msg" -ForegroundColor Green
    $script:PassCount++
}

function Write-Fail($msg) {
    Write-Host "[FAIL] $msg" -ForegroundColor Red
    $script:ErrorCount++
}

function Write-Info($msg) {
    Write-Host "[INFO] $msg" -ForegroundColor Cyan
}

function Test-PythonRuntime {
    param([string]$RepoRoot)

    $pythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $pythonExe)) {
        Write-Fail "Python runtime → .venv not found at $pythonExe"
        return
    }

    try {
        $version = & $pythonExe --version
        if ($version -match "Python 3\.12\.") {
            Write-Pass "Python runtime → $version"
        } else {
            Write-Fail "Python runtime → expected Python 3.12.x, got $version"
        }
    } catch {
        Write-Fail "Python runtime → $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

function Test-ServiceHealth {
    param([string]$Name, [string]$Url)
    try {
        $r = Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 5 -ErrorAction Stop
        if ($r.status -eq "ok") {
            Write-Pass "$Name /health → status=ok, uptime=$($r.uptime_seconds)s"
        } else {
            Write-Fail "$Name /health → unexpected status: $($r.status)"
        }
    } catch {
        Write-Fail "$Name /health → $($_.Exception.Message)"
    }
}

function Test-ServiceReady {
    param([string]$Name, [string]$Url)
    try {
        $r = Invoke-RestMethod -Uri "$Url/ready" -TimeoutSec 5 -ErrorAction Stop
        $checksJson = $r.checks | ConvertTo-Json -Compress
        if ($r.ready) {
            Write-Pass "$Name /ready → ready=true  $checksJson"
        } else {
            Write-Info "$Name /ready → ready=false  $checksJson  (model may still be loading)"
        }
    } catch {
        Write-Fail "$Name /ready → $($_.Exception.Message)"
    }
}

function Test-ServiceVersion {
    param([string]$Name, [string]$Url)
    try {
        $r = Invoke-RestMethod -Uri "$Url/version" -TimeoutSec 5 -ErrorAction Stop
        Write-Pass "$Name /version → $($r.version)"
    } catch {
        Write-Fail "$Name /version → $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------------------
# End-to-end pipeline test
# ---------------------------------------------------------------------------

function Test-ChatEndpoint {
    param([string]$Url)
    $requestId = [System.Guid]::NewGuid().ToString()
    $body = @{
        request_id  = $requestId
        message     = "Koroki, say hello!"
        user_context = @{
            user_id            = "smoke_test_user"
            relationship_score = 30
            is_owner           = $false
            mode               = "auto"
            platform           = "discord"
        }
    } | ConvertTo-Json -Depth 5

    try {
        $r = Invoke-RestMethod `
            -Uri "$Url/v1/chat" `
            -Method POST `
            -Body $body `
            -ContentType "application/json" `
            -TimeoutSec 60 `
            -ErrorAction Stop

        if ($r.text) {
            $preview = $r.text.Substring(0, [Math]::Min(80, $r.text.Length))
            Write-Pass "Orchestrator /v1/chat → got response: `"$preview...`""
        } else {
            Write-Fail "Orchestrator /v1/chat → response has no 'text' field"
        }

        if ($r.timings) {
            Write-Info "Timings: brain_first_token=$($r.timings.t_brain_first_token_ms)ms  total=$($r.timings.t_total_ms)ms"
        }

        # Validate no training leak tokens in the response
        $forbidden = @("Assistant:", "User:", "System:", "<|im_start|>", "<|im_end|>")
        foreach ($tok in $forbidden) {
            if ($r.text -like "*$tok*") {
                Write-Fail "Training leak detected in response: '$tok'"
            }
        }

    } catch {
        Write-Fail "Orchestrator /v1/chat → $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------------------
# Validate request_id rejection (schema test)
# ---------------------------------------------------------------------------

function Test-SchemaValidation {
    param([string]$Url)
    $body = @{ message = "hello" } | ConvertTo-Json  # Missing request_id and user_context
    try {
        $null = Invoke-RestMethod `
            -Uri "$Url/v1/chat" `
            -Method POST `
            -Body $body `
            -ContentType "application/json" `
            -TimeoutSec 5 `
            -ErrorAction Stop
        Write-Fail "Schema validation → accepted malformed request (should have rejected it)"
    } catch {
        if ($_.Exception.Response.StatusCode.value__ -eq 422) {
            Write-Pass "Schema validation → correctly rejected malformed request (422)"
        } else {
            Write-Info "Schema validation → got $($_.Exception.Response.StatusCode.value__) (expected 422)"
        }
    }
}

# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "╔══════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   Koroki 2.0 Smoke Test Suite    ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

$RepoRoot = Split-Path -Parent $PSScriptRoot
Write-Host "── Runtime ─────────────────────────" -ForegroundColor Gray
Test-PythonRuntime $RepoRoot

Write-Host ""
Write-Host "── Health ──────────────────────────" -ForegroundColor Gray
Test-ServiceHealth "Brain"        $BrainUrl
Test-ServiceHealth "TTS"          $TtsUrl
Test-ServiceHealth "Orchestrator" $OrchestratorUrl

Write-Host ""
Write-Host "── Ready ───────────────────────────" -ForegroundColor Gray
Test-ServiceReady "Brain"        $BrainUrl
Test-ServiceReady "TTS"          $TtsUrl
Test-ServiceReady "Orchestrator" $OrchestratorUrl

Write-Host ""
Write-Host "── Version ─────────────────────────" -ForegroundColor Gray
Test-ServiceVersion "Brain"        $BrainUrl
Test-ServiceVersion "TTS"          $TtsUrl
Test-ServiceVersion "Orchestrator" $OrchestratorUrl

Write-Host ""
Write-Host "── Pipeline E2E ────────────────────" -ForegroundColor Gray
Test-ChatEndpoint $OrchestratorUrl

Write-Host ""
Write-Host "── Schema Validation ───────────────" -ForegroundColor Gray
Test-SchemaValidation $OrchestratorUrl

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "────────────────────────────────────" -ForegroundColor Gray
if ($ErrorCount -eq 0) {
    Write-Host "  ALL $PassCount TESTS PASSED" -ForegroundColor Green
} else {
    Write-Host "  $PassCount PASSED  /  $ErrorCount FAILED" -ForegroundColor Red
    exit 1
}
Write-Host ""
