@echo off
setlocal
set "BASE=%~dp0"

where ffmpeg >nul 2>&1
if errorlevel 1 echo [ERROR] FFmpeg PATH me nahi mila. FFmpeg ko PATH me add karein. & pause & exit /b 1

echo ==========================================
echo   AI Video Editor - Insta Reel Maker
echo ==========================================

powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText('%~f0'); $i=$s.IndexOf('###PS'+'START###'); Invoke-Expression $s.Substring($i)"
set "RC=%errorlevel%"

echo.
if "%RC%"=="0" (echo [OK] Video ready: Export\final_reel.mp4) else (echo [ERROR] Kuch problem aayi, upar ka message dekhein.)
pause
exit /b %RC%

###PSSTART###
# ---------------- SETTINGS (yahan badal sakte hain) ----------------
$clips   = 'IMG_2053.MOV','IMG_2054.MOV','IMG_2057.MOV','IMG_2058.MOV'   # merge ka order
$noise   = '-30dB'     # is se kam awaaz = silence (zyada sensitive: -35dB, kam: -25dB)
$minSil  = '0.1'       # 0.1 sec se lambi khamoshi cut hogi
$minSeg  = 0.05        # isse chhota bacha hua tukda skip
$W = 1080; $H = 1920   # Insta Reel 9:16 (1080p)
$FPS = 30
# -------------------------------------------------------------------

$ErrorActionPreference = 'Continue'
$inv  = [Globalization.CultureInfo]::InvariantCulture
$base = $env:BASE
$raw  = Join-Path $base 'Raw video'
$exp  = Join-Path $base 'Export'
$out  = Join-Path $exp 'final_reel.mp4'

if (-not (Test-Path $exp)) { New-Item -ItemType Directory -Path $exp | Out-Null }

$inputs  = @()
$filters = @()
$cin     = ''
$n       = 0      # total segments
$inIdx   = 0      # ffmpeg input index

foreach ($c in $clips) {
    $f = Join-Path $raw $c
    if (-not (Test-Path $f)) { Write-Host "[ERROR] File nahi mili: $f"; exit 1 }

    Write-Host "Silence detect ho raha hai: $c"
    $log = & ffmpeg -hide_banner -nostats -i $f -vn -af "silencedetect=noise=${noise}:d=${minSil}" -f null - 2>&1 | Out-String

    if ($log -notmatch 'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)') { Write-Host "[ERROR] Duration nahi padh paya: $c"; exit 1 }
    $dur = [double]::Parse($matches[1],$inv)*3600 + [double]::Parse($matches[2],$inv)*60 + [double]::Parse($matches[3],$inv)

    $starts = [regex]::Matches($log, 'silence_start:\s*(-?[\d\.]+)')
    $ends   = [regex]::Matches($log, 'silence_end:\s*(-?[\d\.]+)')

    # silence ke bahar wale (bolne wale) segments nikalo
    $segs   = @()
    $cursor = 0.0
    for ($i = 0; $i -lt $starts.Count; $i++) {
        $s = [Math]::Max(0.0, [double]::Parse($starts[$i].Groups[1].Value,$inv))
        if (($s - $cursor) -ge $minSeg) { $segs += ,@($cursor, $s) }
        if ($i -lt $ends.Count) { $cursor = [double]::Parse($ends[$i].Groups[1].Value,$inv) } else { $cursor = $dur }
    }
    if (($dur - $cursor) -ge $minSeg) { $segs += ,@($cursor, $dur) }

    $k = $segs.Count
    Write-Host ("   -> {0} tukde bache (total {1:N1}s me se)" -f $k, $dur)
    if ($k -eq 0) { Write-Host "   -> poori clip silent hai, skip."; continue }

    $inputs += '-i'; $inputs += $f

    # video: 1080x1920 me fit + pad
    $vchain = "[${inIdx}:v]scale=${W}:${H}:force_original_aspect_ratio=decrease,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=${FPS},format=yuv420p"
    $achain = "[${inIdx}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    $vl = @(); $al = @()
    for ($j = 0; $j -lt $k; $j++) { $vl += "[v${inIdx}_${j}]"; $al += "[a${inIdx}_${j}]" }

    if ($k -eq 1) { $vchain += $vl[0]; $achain += $al[0] }
    else          { $vchain += ",split=$k" + ($vl -join ''); $achain += ",asplit=$k" + ($al -join '') }
    $filters += $vchain
    $filters += $achain

    for ($j = 0; $j -lt $k; $j++) {
        $ss = $segs[$j][0].ToString('F3',$inv)
        $ee = $segs[$j][1].ToString('F3',$inv)
        $filters += "$($vl[$j])trim=start=${ss}:end=${ee},setpts=PTS-STARTPTS[sv${n}]"
        $filters += "$($al[$j])atrim=start=${ss}:end=${ee},asetpts=PTS-STARTPTS[sa${n}]"
        $cin += "[sv${n}][sa${n}]"
        $n++
    }
    $inIdx++
}

if ($n -eq 0) { Write-Host "[ERROR] Koi bhi tukda nahi bacha. noise threshold check karein."; exit 1 }

$filters += "${cin}concat=n=${n}:v=1:a=1[outv][outa]"
$fc = $filters -join ';'

if ($fc.Length -gt 30000) {
    Write-Host "[ERROR] Bahut zyada tukde ($n). noise ko -35dB karke ya minSil badha kar dobara chalayein."
    exit 1
}

Write-Host ""
Write-Host "Merge + encode shuru ($n tukde)..."

$ffArgs = @('-hide_banner') + $inputs + @(
    '-filter_complex', $fc,
    '-map', '[outv]', '-map', '[outa]',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-r', "$FPS",
    '-c:a', 'aac', '-b:a', '192k',
    '-movflags', '+faststart',
    '-y', $out
)
& ffmpeg @ffArgs
if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] FFmpeg fail hua (code $LASTEXITCODE)"; exit $LASTEXITCODE }

Write-Host ""
Write-Host "Saved: $out"
exit 0
