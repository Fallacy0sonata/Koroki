# Koroki Discord Bot Launcher
# Phase 7: First Contact
# This script starts the Discord bot connected to the running Orchestrator

[CmdletBinding()]
param()

$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

Write-Host ""
Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   Koroki Discord Bot - Phase 7           ║" -ForegroundColor Cyan
Write-Host "║         First Contact Protocol           ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# --- Validate venv ---
if (-not (Test-Path $VenvPython)) {
    Write-Host "[ERROR] Python venv not found at: $VenvPython" -ForegroundColor Red
    Write-Host "Run: py -3.12 -m venv .venv && .venv\Scripts\pip install -e ." -ForegroundColor Yellow
    exit 1
}

# --- Validate .env ---
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Host "[ERROR] .env file not found!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Steps to set up Discord bot:" -ForegroundColor Yellow
    Write-Host "  1. Copy .env.example to .env"
    Write-Host "  2. Create a Discord bot at: https://discord.com/developers/applications"
    Write-Host "  3. Copy your bot token to DISCORD_TOKEN (or DISCORD_BOT_TOKEN) in .env"
    Write-Host "  4. Get your Discord user ID and set OWNER_DISCORD_ID (or DISCORD_OWNER_ID) in .env"
    Write-Host "  5. Invite the bot to your server with the OAuth2 URL"
    Write-Host ""
    exit 1
}

# --- Check for discord.py ---
Write-Host "[INFO] Checking for discord.py..." -ForegroundColor Cyan
$DiscordCheck = & $VenvPython -c "import discord; print(discord.__version__)" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] discord.py not found, installing..." -ForegroundColor Yellow
    & $VenvPython -m pip install discord.py -q
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] discord.py installed" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Failed to install discord.py" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[OK] discord.py $DiscordCheck" -ForegroundColor Green
}

# --- Load .env and validate required keys ---
$EnvVars = @{}
Get-Content $EnvFile | ForEach-Object {
    if ($_ -match "^\s*([^#\s][^=]*)=(.*)$") {
        $key = $matches[1].Trim()
        $val = $matches[2].Trim().Trim('"').Trim("'")
        $EnvVars[$key] = $val
    }
}

$BotToken = $EnvVars["DISCORD_TOKEN"]
if (-not $BotToken) {
    $BotToken = $EnvVars["DISCORD_BOT_TOKEN"]
}

if (-not $BotToken -or $BotToken -eq "your_discord_bot_token_here") {
    Write-Host "[ERROR] DISCORD_TOKEN/DISCORD_BOT_TOKEN not set in .env" -ForegroundColor Red
    exit 1
}

$OwnerId = $EnvVars["OWNER_DISCORD_ID"]
if (-not $OwnerId) {
    $OwnerId = $EnvVars["DISCORD_OWNER_ID"]
}

if (-not $OwnerId -or $OwnerId -eq "your_own_discord_user_id_here") {
    Write-Host "[ERROR] OWNER_DISCORD_ID/DISCORD_OWNER_ID not set in .env" -ForegroundColor Red
    exit 1
}

$GuildIds = $EnvVars["DISCORD_GUILD_IDS"]
if (-not $GuildIds) {
    $GuildIds = $EnvVars["DISCORD_GUILD_ID"]
}

Write-Host "[OK] .env validated" -ForegroundColor Green
Write-Host "     DISCORD_TOKEN: ***$($BotToken.Substring($BotToken.Length - 8))" -ForegroundColor Gray
Write-Host "     OWNER_DISCORD_ID: $OwnerId" -ForegroundColor Gray
if ($GuildIds) {
    Write-Host "     DISCORD_GUILD_IDS: $GuildIds" -ForegroundColor Gray
} else {
    Write-Host "     DISCORD_GUILD_IDS: (not set, global command sync)" -ForegroundColor Gray
}
Write-Host ""

# --- Verify Orchestrator is running ---
Write-Host "[INFO] Checking Orchestrator connectivity..." -ForegroundColor Cyan
try {
    $response = Invoke-WebRequest `
        -Uri "http://127.0.0.1:9882/health" `
        -TimeoutSec 3 `
        -ErrorAction Stop
    $health = $response.Content | ConvertFrom-Json
    Write-Host "[OK] Orchestrator online (uptime: $($health.uptime_seconds)s)" -ForegroundColor Green
} catch {
    Write-Host "[WARN] Orchestrator not responding at http://127.0.0.1:9882" -ForegroundColor Yellow
    Write-Host "       Ensure the Orchestrator service is running before starting the bot." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "Press Enter to continue anyway (or Ctrl+C to cancel)"
}

Write-Host ""
Write-Host "════════════════════════════════════════════" -ForegroundColor Gray
Write-Host "Starting Discord Bot..." -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════" -ForegroundColor Gray
Write-Host ""

# --- Start the bot ---
& $VenvPython "$Root\discord_bot.py"
