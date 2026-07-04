$out = "C:\Users\Shinn\Desktop\Koroki\cowork_tests\overnight_worldstate.jsonl"
$host.UI.RawUI.WindowTitle = "Koroki overnight monitor (cowork)"
Write-Host "Overnight monitor running - snapshot every 10 min -> $out"
while ($true) {
    try {
        $ws = curl.exe -s -m 15 http://127.0.0.1:9882/v1/worldstate
        if ($ws) {
            $line = '{"snap_ts":"' + (Get-Date -Format o) + '","worldstate":' + $ws + '}'
            Add-Content -Path $out -Value $line -Encoding UTF8
        } else {
            Add-Content -Path $out -Value ('{"snap_ts":"' + (Get-Date -Format o) + '","error":"no response"}') -Encoding UTF8
        }
    } catch {
        Add-Content -Path $out -Value ('{"snap_ts":"' + (Get-Date -Format o) + '","error":"exception"}') -Encoding UTF8
    }
    Start-Sleep -Seconds 600
}
