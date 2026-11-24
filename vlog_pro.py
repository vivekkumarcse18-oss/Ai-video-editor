import os, sys, glob, re, subprocess, shutil
from build_video import crop_and_pro_fx, get_export_resolution_and_bitrate

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
BASE_DIR = os.environ.get("WORKSPACE_DIR", SCRIPT_DIR)
RAW_DIR = os.path.join(BASE_DIR, "Raw video")
EXPORT_DIR = os.path.join(BASE_DIR, "Export")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)

def parse_silence_segments(video_path, noise_threshold="-30dB", min_silence=0.15):
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", video_path,
        "-vn",
        "-af", f"silencedetect=noise={noise_threshold}:d={min_silence}",
        "-f", "null", "-"
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="ignore")
    log = proc.stderr

    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", log)
    if not dur_match:
        return [(0.0, 30.0)]
    hours, mins, secs = float(dur_match.group(1)), float(dur_match.group(2)), float(dur_match.group(3))
    total_duration = hours * 3600 + mins * 60 + secs

    starts = [float(m.group(1)) for m in re.finditer(r"silence_start:\s*(-?[\d\.]+)", log)]
    ends = [float(m.group(1)) for m in re.finditer(r"silence_end:\s*(-?[\d\.]+)", log)]

    speech_segments = []
    cursor = 0.0
    for i in range(len(starts)):
        s = max(0.0, starts[i])
        if (s - cursor) >= 0.12:
            speech_segments.append((cursor, s))
        if i < len(ends):
            cursor = ends[i]
        else:
            cursor = total_duration

    if (total_duration - cursor) >= 0.12:
        speech_segments.append((cursor, total_duration))

    if not speech_segments:
        speech_segments = [(0.0, min(total_duration, 60.0))]

    return speech_segments

def run_vlog_pro():
    target_w, target_h, bitrate, quality_label = get_export_resolution_and_bitrate(RAW_DIR)
    print(f"🎬 Vlog Pro Export Mode: {quality_label} -> Target Canvas: {target_w}x{target_h} @ {bitrate}")

    print("[1/4] Scanning Raw video folder for talking-head vlog clips...")
    raws = sorted([
        f for f in glob.glob(os.path.join(RAW_DIR, "*.*"))
        if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm"))
    ])

    if not raws:
        vids = sorted([
            f for f in glob.glob(os.path.join(BASE_DIR, "videos", "*.*"))
            if f.lower().endswith((".mp4", ".mov", ".mkv"))
        ])
        if vids:
            print(f"No files in Raw video, falling back to {len(vids)} clips in videos/ folder.")
            raws = vids
        else:
            raise FileNotFoundError("Raw video folder mein apni clips (.mp4, .mov, .mkv) daalo!")

    print(f"Found {len(raws)} clip(s): {[os.path.basename(r) for r in raws]}")
    print("[2/4] Detecting silence & cutting dead air (noise: -30dB, min: 0.15s)...")

    inputs = []
    filters = []
    cin = ""
    total_segments = 0
    in_idx = 0

    for clip_path in raws:
        clip_name = os.path.basename(clip_path)
        segs = parse_silence_segments(clip_path)
        k = len(segs)
        print(f"   -> {clip_name}: Kept {k} active speech segments")

        inputs.extend(["-i", clip_path])
        vchain = f"[{in_idx}:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p"
        achain = f"[{in_idx}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"

        vl = [f"[v{in_idx}_{j}]" for j in range(k)]
        al = [f"[a{in_idx}_{j}]" for j in range(k)]

        if k == 1:
            vchain += vl[0]
            achain += al[0]
        else:
            vchain += f",split={k}" + "".join(vl)
            achain += f",asplit={k}" + "".join(al)

        filters.append(vchain)
        filters.append(achain)

        for j in range(k):
            ss, ee = segs[j]
            # Dynamic subtle zoom punch for alternating cuts
            if j % 2 == 1:
                vfilter = f"{vl[j]}trim=start={ss:.3f}:end={ee:.3f},setpts=PTS-STARTPTS,scale=1.08*iw:-1,crop={target_w}:{target_h}[sv{total_segments}]"
            else:
                vfilter = f"{vl[j]}trim=start={ss:.3f}:end={ee:.3f},setpts=PTS-STARTPTS[sv{total_segments}]"

            afilter = f"{al[j]}atrim=start={ss:.3f}:end={ee:.3f},asetpts=PTS-STARTPTS[sa{total_segments}]"
            filters.append(vfilter)
            filters.append(afilter)
            cin += f"[sv{total_segments}][sa{total_segments}]"
            total_segments += 1

        in_idx += 1

    if total_segments == 0:
        raise RuntimeError("No active speech segments detected in any clips.")

    print(f"[3/4] Concatenating {total_segments} dynamic cuts with {target_w}x{target_h} vertical formatting...")
    filters.append(f"{cin}concat=n={total_segments}:v=1:a=1[mergedv][mergeda]")

    # Check for optional background music
    bgm_path = os.path.join(AUDIO_DIR, "bgm.mp3")
    has_bgm = os.path.exists(bgm_path)
    if has_bgm:
        print("[4/4] Ducking background music under speech audio...")
        inputs.extend(["-stream_loop", "-1", "-i", bgm_path])
        bgm_idx = in_idx
        filters.append(
            f"[{bgm_idx}:a]volume=0.12[bgm];"
            f"[bgm][mergeda]sidechaincompress=threshold=0.03:ratio=5:attack=40:release=300[ducked];"
            f"[mergeda][ducked]amix=inputs=2:duration=first:dropout_transition=2,loudnorm=I=-14:TP=-1.5:LRA=7[outa];"
            f"[mergedv]null[outv]"
        )
        map_v, map_a = "[outv]", "[outa]"
    else:
        print("[4/4] Normalizing speech audio with broadcast loudnorm...")
        filters.append("[mergeda]loudnorm=I=-14:TP=-1.5:LRA=7[outa]")
        map_v, map_a = "[mergedv]", "[outa]"

    out_file = os.path.join(EXPORT_DIR, "pro_self_video.mp4")
    alt_file = os.path.join(EXPORT_DIR, "final_reel.mp4")
    filter_complex = ";".join(filters)

    buf_size = f"{int(bitrate.replace('k',''))*2}k"
    ff_cmd = [
        "ffmpeg", "-y", "-hide_banner",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", map_v, "-map", map_a,
        "-c:v", "libx264", "-preset", "fast",
        "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", buf_size,
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_file
    ]

    print(f"Encoding master pro vlog at {target_w}x{target_h} ({bitrate})...")
    res = subprocess.run(ff_cmd, check=True)
    if os.path.exists(out_file):
        try:
            shutil.copy2(out_file, alt_file)
        except Exception:
            pass
        print(f"DONE! Pro Self-Vlog Editor exported to: {out_file}")
    else:
        raise RuntimeError("Failed to generate pro_self_video.mp4")

if __name__ == "__main__":
    run_vlog_pro()
