# kill-backend.ps1
# 8002 포트의 uvicorn 백엔드 서버를 강제 종료한다.
# Ctrl+C 로 안 죽을 때(모델 로딩 중 / OCR·임베딩 워커 스레드 점유) 사용.
#
# 사용법:  backend\ 에서  .\kill-backend.ps1

$port = 8002
$killed = @()

# 1) uvicorn / app.main 커맨드라인을 가진 python 프로세스 종료
Get-CimInstance Win32_Process -Filter "name='python.exe'" |
    Where-Object { $_.CommandLine -like '*uvicorn*' -or $_.CommandLine -like '*app.main*' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        $killed += $_.ProcessId
    }

# 2) 혹시 남은 포트 점유 프로세스도 종료
$conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
foreach ($c in $conns) {
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    $killed += $c.OwningProcess
}

Start-Sleep -Milliseconds 800

# 3) 결과 보고
$killed = $killed | Sort-Object -Unique
if ($killed.Count -gt 0) {
    Write-Host ("종료된 PID: " + ($killed -join ', ')) -ForegroundColor Yellow
} else {
    Write-Host "종료할 백엔드 프로세스가 없었습니다." -ForegroundColor Gray
}

$still = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($still) {
    Write-Host ("경고: 포트 $port 아직 LISTEN 중 (pid=$($still.OwningProcess))") -ForegroundColor Red
} else {
    Write-Host ("포트 $port 해제됨 — 이제 다시 띄울 수 있습니다.") -ForegroundColor Green
}
