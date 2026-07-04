$python = "C:\Users\Shinn\Desktop\Koroki\.venv_diffsinger\Scripts\python.exe"
$trainDir = "C:\Users\Shinn\Desktop\Koroki\experiments\diffsinger\DiffSinger"
$config = "configs/koroki_multispk_v5.yaml"

Push-Location $trainDir
try {
    & $python scripts/binarize.py --config $config
} finally {
    Pop-Location
}
