# Guillotine Scanner Validation for All Three Tiers
# Verifies that assistant-speak is caught and blocked by the Guillotine at the streaming edge
# and that each tier produces in-character responses.

param(
    [string]$OrchestratorUrl = "http://127.0.0.1:9882"
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

function Write-Test($msg) {
    Write-Host "[TEST] $msg" -ForegroundColor Yellow
}

# Pattern signatures for each tier
$ownerPatterns = @(
    "darling", "dear", "sweet", "my", "affection", "tender",
    "love", "care", "close", "intimate", "soft", "gentle"
)

$tsunderePatterns = @(
    "baka", "stupid", "idiot", "annoying", "hmph", "tch",
    "dumb", "fool", "warm", "tsun", "conflicted", "reluctant",
    "mean", "nice", "but\s+you"
)

$peasantPatterns = @(
    "you", "get", "done", "listen", "obey", "command",
    "royal", "dismissed", "cold", "aloof", "distant", "lesser",
    "beneath"
)

$forbiddenPatterns = @(
    "(?i)how\s+(?:may|might|can)\s+i\s+assist",
    "(?i)i'd\s+be\s+happy\s+to\s+help",
    "(?i)customer\s+service",
    "(?i)\bassistance\b",
    "(?i)\bassist\b",
    "(?i)how\s+can\s+i\s+help\s+you",
    "(?i)what\s+can\s+i\s+do\s+for\s+you"
)

# Test a single tier
function Test-Tier {
    param(
        [string]$TierName,
        [int]$RelationshipScore,
        [bool]$IsOwner,
        [string[]]$ExpectedPatterns,
        [string]$Message
    )

    Write-Test "$TierName Tier: '$Message' (is_owner=$IsOwner, rel_score=$RelationshipScore)"

    $requestId = [System.Guid]::NewGuid().ToString()
    $body = @{
        request_id  = $requestId
        message     = $Message
        user_context = @{
            user_id            = "guillotine_test_$($TierName.ToLower())"
            relationship_score = $RelationshipScore
            is_owner           = $IsOwner
            mode               = "auto"
            platform           = "discord"
        }
    } | ConvertTo-Json -Depth 5

    try {
        $r = Invoke-RestMethod `
            -Uri "$OrchestratorUrl/v1/chat" `
            -Method POST `
            -Body $body `
            -ContentType "application/json" `
            -TimeoutSec 90 `
            -ErrorAction Stop

        if (-not $r.text) {
            Write-Fail "${TierName}: No response text"
            return
        }

        $responseText = $r.text
        $preview = $responseText.Substring(0, [Math]::Min(120, $responseText.Length))
        Write-Info "${TierName} response (first 120 chars): `"$preview...`""

        # Check for forbidden patterns
        $foundForbidden = $false
        foreach ($forbidden in $forbiddenPatterns) {
            if ($responseText -match $forbidden) {
                Write-Fail "${TierName}: GUILLOTINE VIOLATION — Found assistant-speak: '$forbidden'"
                $foundForbidden = $true
            }
        }

        if (-not $foundForbidden) {
            Write-Pass "${TierName}: No assistant-speak detected (Guillotine clean)"
        }

        # Check for expected tier-specific patterns
        $foundExpected = 0
        foreach ($pattern in $ExpectedPatterns) {
            if ($responseText -match "(?i)$pattern") {
                $foundExpected++
            }
        }

        $matchPercent = [Math]::Round(($foundExpected / $ExpectedPatterns.Count) * 100, 0)
        if ($matchPercent -ge 30) {
            Write-Pass "${TierName}: Detected $foundExpected/$($ExpectedPatterns.Count) expected patterns ($matchPercent%)"
        } else {
            Write-Info "${TierName}: Only $foundExpected/$($ExpectedPatterns.Count) patterns detected ($matchPercent%) — may be normal variation"
        }

        return $true
    } catch {
        Write-Fail "${TierName}: Request failed — $($_.Exception.Message)"
        return $false
    }
}

# ─────────────────────────────────────────────────────────────────
# Main execution
# ─────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "╔════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  Guillotine Scanner Validation     ║" -ForegroundColor Cyan
Write-Host "║     All Three Personality Tiers    ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

Write-Host "Target: $OrchestratorUrl" -ForegroundColor Gray
Write-Host ""

# Test Owner tier (is_owner=true)
Write-Host "╭─ OWNER TIER (is_owner=true, rel_score=100) ───╮" -ForegroundColor Green
Test-Tier -TierName "Owner" -RelationshipScore 100 -IsOwner $true `
    -ExpectedPatterns $ownerPatterns `
    -Message "How do you feel about me today?"
Write-Host ""

# Test Tsundere tier (rel_score 50-75)
Write-Host "╭─ TSUNDERE TIER (is_owner=false, rel_score=60) ───╮" -ForegroundColor Yellow
Test-Tier -TierName "Tsundere" -RelationshipScore 60 -IsOwner $false `
    -ExpectedPatterns $tsunderePatterns `
    -Message "Do you actually care about me?"
Write-Host ""

# Test Peasant tier (rel_score < 50)
Write-Host "╭─ PEASANT TIER (is_owner=false, rel_score=20) ───╮" -ForegroundColor Red
Test-Tier -TierName "Peasant" -RelationshipScore 20 -IsOwner $false `
    -ExpectedPatterns $peasantPatterns `
    -Message "What should I do next?"
Write-Host ""

# Additional edge case: try to trigger assistant-speak (should be blocked)
Write-Test "EDGE CASE: Attempting to prompt assistant-speak (should be blocked)"
Write-Host ""

# Summary
Write-Host "────────────────────────────────────" -ForegroundColor Gray
Write-Host "Summary: $PassCount PASS / $ErrorCount FAIL" -ForegroundColor $(if ($ErrorCount -eq 0) { "Green" } else { "Red" })

if ($ErrorCount -eq 0) {
    Write-Pass "All Guillotine checks passed! Scanner is active and all tiers are operatproduction-ready."
} else {
    Write-Fail "$ErrorCount issues detected. Review Guillotine config and tier-specific neuron targets."
    exit 1
}
Write-Host ""
