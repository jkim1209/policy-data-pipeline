<#
    정기 갱신 래퍼. 작업 스케줄러가 이 파일을 부릅니다.

    SKILL.md 「정기 갱신을 실행할 때」
      1. python run.py 를 실행한다
      2. 검증에서 중단되면 원인을 보고하고 멈춘다. 넘어가지 않는다
      3. "확인 필요"가 나오면 원문을 대조한다
      4. 코드북 4절에 미확인 항목이 있으면 알린다

    이 스크립트가 하는 일
      - 실행 전 산출물을 보존합니다. 검증 실패로 중단되면 지난 성공분이 남습니다
      - 전체 출력을 logs/monthly/ 에 남깁니다. 무인 실행이라 화면이 없습니다
      - run.py 의 종료 코드를 그대로 돌려줍니다. 실패하면 작업 스케줄러 기록에 남습니다
      - 실패 시 logs/monthly/LAST_FAILURE.txt 를 남겨 다음 사람이 바로 보게 합니다
#>
[CmdletBinding()]
param(
    # 인증키 없이 스냅샷으로 돌릴 때 사용합니다. 점검용입니다.
    [switch]$Offline,
    # 검증 실패 상황을 일부러 만듭니다. 경보 경로가 살아 있는지 확인할 때만 씁니다.
    [switch]$BreakCheck,
    # 보관할 지난 산출물 사본 개수.
    [int]$KeepBackups = 6
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
$logDir = Join-Path $repo 'logs\monthly'
$stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$log = Join-Path $logDir "$stamp.log"
$failMark = Join-Path $logDir 'LAST_FAILURE.txt'

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
if (-not (Test-Path $python)) {
    "python 실행 파일이 없습니다: $python" | Tee-Object -FilePath $log
    exit 2
}

# 산출물 보존 — 검증이 실패해도 지난 성공분을 잃지 않습니다.
$out = Join-Path $repo 'out'
if (Test-Path $out) {
    $keep = Join-Path $repo "logs\monthly\out_$stamp"
    Copy-Item -Path $out -Destination $keep -Recurse -Force
}

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

# Windows PowerShell 5.1 은 네이티브 명령의 출력을 콘솔 코드페이지(한국어 Windows 는
# cp949)로 디코딩합니다. 파이썬이 UTF-8 로 내보낸 한글이 로그에서 깨집니다.
# 무인 실행에서는 이 로그가 유일한 기록이라 여기서 맞춰줍니다.
$prevOut = [Console]::OutputEncoding
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$argsList = @('run.py')
if ($Offline)    { $argsList += '--offline' }
if ($BreakCheck) { $argsList += '--break-check' }

"=== 정기 갱신 $stamp ===" | Tee-Object -FilePath $log
"저장소: $repo"            | Tee-Object -FilePath $log -Append
"명령: python $($argsList -join ' ')" | Tee-Object -FilePath $log -Append
""                          | Tee-Object -FilePath $log -Append

Push-Location $repo
try {
    & $python @argsList 2>&1 | Tee-Object -FilePath $log -Append
    $code = $LASTEXITCODE
} finally {
    Pop-Location
    [Console]::OutputEncoding = $prevOut
}

""                                   | Tee-Object -FilePath $log -Append
"종료 코드: $code"                    | Tee-Object -FilePath $log -Append

if ($code -ne 0) {
    # 검증 중단이면 산출물이 갱신되지 않았습니다. 다음 사람이 바로 알게 남깁니다.
    @(
        "정기 갱신 실패 — $stamp",
        "종료 코드: $code",
        "로그: $log",
        "",
        "검증 중단이면 산출물은 갱신되지 않았습니다. out/ 는 직전 성공분입니다.",
        "검사를 느슨하게 고쳐 통과시키지 마세요 (CLAUDE.md).",
        "임계값을 바꿔야 한다면 근거를 정리해 확인을 받으십시오."
    ) | Set-Content -Path $failMark -Encoding utf8
    "실패 표시: $failMark" | Tee-Object -FilePath $log -Append
} elseif (Test-Path $failMark) {
    Remove-Item $failMark -Force
}

# 코드북 4절 미확인 항목은 성공해도 사람이 봐야 합니다.
$codebook = Join-Path $repo 'out\codebook.md'
if ((Test-Path $codebook) -and $code -eq 0) {
    $cb = Get-Content $codebook -Raw -Encoding utf8
    if ($cb -match '미확인') {
        "주의: 코드북 4절에 미확인 항목이 있습니다. out/codebook.md 를 확인하세요." |
            Tee-Object -FilePath $log -Append
    }
}

# 지난 산출물 사본 정리. 매월 쌓이면 저장소가 불어납니다.
Get-ChildItem -Path $logDir -Directory -Filter 'out_*' -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending |
    Select-Object -Skip $KeepBackups |
    ForEach-Object {
        Remove-Item $_.FullName -Recurse -Force
        "오래된 산출물 사본 삭제: $($_.Name)" | Tee-Object -FilePath $log -Append
    }

exit $code
