# Upload code + private pack to the rehearsal pod.
#   powershell -File sync_to_pod.ps1 -PodIp 1.2.3.4 -PodPort 12345
# Code: services/, shared/, experiments/index-tts adapter, config, pod_live scripts.
# Private: the LoRA selected by config/settings.yaml + voice sample wavs.
# NO .env, NO data/, NO venvs.
param(
    [Parameter(Mandatory)][string]$PodIp,
    [Parameter(Mandatory)][int]$PodPort,
    [string]$Key = "$env:USERPROFILE\.ssh\runpod_battery"
)
$ErrorActionPreference = "Stop"
$Root = "C:\Users\Shinn\Desktop\Koroki"
$ssh = "ssh -i `"$Key`" -p $PodPort -o StrictHostKeyChecking=accept-new root@$PodIp"
$settingsText = Get-Content (Join-Path $Root "config\settings.yaml") -Raw
$loraMatch = [regex]::Match($settingsText, '(?m)^\s*lora_dir:\s*"adapters/([^"]+)"')
if (-not $loraMatch.Success) { throw "Could not find models.brain.exl2.lora_dir in settings.yaml" }
$LoraName = $loraMatch.Groups[1].Value
$LoraPath = Join-Path $Root "adapters\$LoraName"
if (-not (Test-Path $LoraPath)) { throw "Configured LoRA does not exist: $LoraPath" }
Write-Host "    private LoRA: $LoraName" -ForegroundColor DarkGray

Write-Host "[1/3] packing code..." -ForegroundColor Yellow
$tar = "$env:TEMP\koroki_pod_code.tar.gz"
Push-Location $Root
tar -czf $tar `
    --exclude="__pycache__" --exclude="*.pyc" --exclude=".venv*" `
    services shared config scripts/pod_live experiments/index-tts/adapter.py `
    experiments/cosyvoice/adapter.py voice_samples/EN_sample.wav
Pop-Location

Write-Host "[2/3] uploading code + private pack..." -ForegroundColor Yellow
Invoke-Expression "$ssh `"mkdir -p /workspace/koroki /workspace/private`""
scp -i $Key -P $PodPort $tar "root@${PodIp}:/workspace/koroki_code.tar.gz"
scp -i $Key -P $PodPort -r $LoraPath "root@${PodIp}:/workspace/private/"
# the starmie tokenizer trap: vanilla HF moondream2 lacks our fixed tokenizer —
# vision 500s on every describe without it (live 2026-07-08)
Invoke-Expression "$ssh `"mkdir -p /workspace/models/moondream2-2025-06-21`""
scp -i $Key -P $PodPort "$Root\tools\models\moondream2-2025-06-21\tokenizer_starmie.json" "root@${PodIp}:/workspace/models/moondream2-2025-06-21/"
Invoke-Expression "$ssh `"cd /workspace/koroki && tar -xzf /workspace/koroki_code.tar.gz && rm /workspace/koroki_code.tar.gz && ls`""

Write-Host "[3/3] done. Next, on the pod:" -ForegroundColor Green
Write-Host "  bash /workspace/koroki/scripts/pod_live/pod_setup.sh"
Write-Host "  bash /workspace/koroki/scripts/pod_live/pod_run.sh"
Remove-Item $tar -Force
