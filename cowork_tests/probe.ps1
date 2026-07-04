$out = "C:\Users\Shinn\Desktop\Koroki\cowork_tests\probe_out.txt"
"=== probe $(Get-Date) ===" | Out-File $out
"--- health ---" | Out-File $out -Append
curl.exe -s -m 10 http://127.0.0.1:9882/health | Out-File $out -Append
"" | Out-File $out -Append
"--- ready ---" | Out-File $out -Append
curl.exe -s -m 10 http://127.0.0.1:9882/ready | Out-File $out -Append
"" | Out-File $out -Append
"--- worldstate ---" | Out-File $out -Append
curl.exe -s -m 10 http://127.0.0.1:9882/v1/worldstate | Out-File $out -Append
"" | Out-File $out -Append
"--- brain health ---" | Out-File $out -Append
curl.exe -s -m 10 http://127.0.0.1:9881/health | Out-File $out -Append
"" | Out-File $out -Append
"--- indextts health ---" | Out-File $out -Append
curl.exe -s -m 10 http://127.0.0.1:9000/health | Out-File $out -Append
"" | Out-File $out -Append
"=== DONE probe $(Get-Date) ===" | Out-File $out -Append
