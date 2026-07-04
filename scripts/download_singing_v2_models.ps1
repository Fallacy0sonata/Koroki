# Download Seed-VC model weights for Koroki Singing v2.
# Run this once before using /sing with the Seed-VC adapter.
# All models are cached to experiments\singing-v2\seed-vc\checkpoints\
#
# Models downloaded (~2.4 GB total):
#   Plachta/Seed-VC         DiT + config (~783 MB)
#   lj1995/VoiceConversionWebUI  rmvpe.pt (~173 MB)
#   funasr/campplus         campplus_cn_common.bin (~27 MB)
#   nvidia/bigvgan_v2_44khz_128band_512x  (~466 MB)
#   openai/whisper-small    (~922 MB)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

$VenvPython = Join-Path $RepoRoot ".venv_singing_v2\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "ERROR: .venv_singing_v2 not found. Run easy_start_singing_v2_adapter.ps1 setup first." -ForegroundColor Red
    exit 1
}

$SeedVcDir = Join-Path $RepoRoot "experiments\singing-v2\seed-vc"
if (-not (Test-Path $SeedVcDir)) {
    Write-Host "ERROR: Seed-VC not cloned at $SeedVcDir" -ForegroundColor Red
    exit 1
}

Write-Host "Downloading Seed-VC models (~2.4 GB)..." -ForegroundColor Cyan
Write-Host "This is a one-time download. Models cached to experiments\singing-v2\seed-vc\checkpoints\" -ForegroundColor Yellow
Write-Host ""

$DownloadScript = @"
import os, sys
sys.path.insert(0, r'$($SeedVcDir.Replace('\','\\'))')
os.chdir(r'$($SeedVcDir.Replace('\','\\'))')
os.environ['HF_HUB_CACHE'] = './checkpoints/hf_cache'

from huggingface_hub import hf_hub_download
import os

os.makedirs('./checkpoints', exist_ok=True)

print('[1/5] Downloading Seed-VC DiT checkpoint (783 MB)...')
hf_hub_download('Plachta/Seed-VC', 'DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth', cache_dir='./checkpoints')
hf_hub_download('Plachta/Seed-VC', 'config_dit_mel_seed_uvit_whisper_base_f0_44k.yml', cache_dir='./checkpoints')
print('[1/5] Done.')

print('[2/5] Downloading RMVPE f0 extractor (173 MB)...')
hf_hub_download('lj1995/VoiceConversionWebUI', 'rmvpe.pt', cache_dir='./checkpoints')
print('[2/5] Done.')

print('[3/5] Downloading CAMPPlus speaker encoder (27 MB)...')
hf_hub_download('funasr/campplus', 'campplus_cn_common.bin', cache_dir='./checkpoints')
print('[3/5] Done.')

print('[4/5] Downloading BigVGAN vocoder (466 MB)...')
from modules.bigvgan import bigvgan
bigvgan.BigVGAN.from_pretrained('nvidia/bigvgan_v2_44khz_128band_512x', use_cuda_kernel=False)
print('[4/5] Done.')

print('[5/5] Downloading Whisper-small encoder (922 MB)...')
from transformers import WhisperModel, AutoFeatureExtractor
WhisperModel.from_pretrained('openai/whisper-small', torch_dtype='float16')
AutoFeatureExtractor.from_pretrained('openai/whisper-small')
print('[5/5] Done.')

print('')
print('All Seed-VC models downloaded successfully.')
"@

& $VenvPython -c $DownloadScript
if ($LASTEXITCODE -ne 0) {
    Write-Host "Download failed. Check output above." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Done. Start the adapter with: .\scripts\easy_start_singing_v2_adapter.ps1" -ForegroundColor Green
