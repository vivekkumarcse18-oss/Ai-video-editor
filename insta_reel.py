import os, glob, subprocess
import numpy as np
import PIL.Image
if not hasattr(PIL.Image, "ANTIALIAS"):
    PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips
from build_video import crop_and_pro_fx, get_export_resolution_and_bitrate

import sys
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
BASE_DIR = os.environ.get("WORKSPACE_DIR", SCRIPT_DIR)
RAW_DIR = os.path.join(BASE_DIR, "Raw video")
REF_DIR = os.path.join(BASE_DIR, "References")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
EXPORT_DIR = os.path.join(BASE_DIR, "Export")
for d in [RAW_DIR, REF_DIR, AUDIO_DIR, EXPORT_DIR]:
    os.makedirs(d, exist_ok=True)

def fetch_reference_style():
    ref_link_file = os.path.join(REF_DIR, "style_link.txt")
    ref_vid = os.path.join(REF_DIR, "ref_sample.mp4")
    if os.path.exists(ref_link_file):
        url = open(ref_link_file, "r", encoding="utf-8").read().strip()
        if url.startswith("http"):
            print("1. Downloading Reference Style Reel...")
            if os.path.exists(ref_vid):
                os.remove(ref_vid)
            subprocess.run([
                "yt-dlp", "-f", "best[ext=mp4]/best", "-o", ref_vid, url
            ], check=True)
            if os.path.exists(ref_vid):
                rc = VideoFileClip(ref_vid)
                dur = rc.duration
                rc.close()
                cut_speed = 1.3 if dur <= 12 else (1.8 if dur <= 22 else 2.4)
                return ref_vid, cut_speed, dur
    return None, 1.8, 25.0

def get_insta_audio(ref_vid):
    link_file = os.path.join(AUDIO_DIR, "insta_link.txt")
    insta_mp3 = os.path.join(AUDIO_DIR, "insta_bgm.mp3")
    custom_bgm = os.path.join(AUDIO_DIR, "bgm.mp3")

    if os.path.exists(link_file):
        url = open(link_file, "r", encoding="utf-8").read().strip()
        if url.startswith("http"):
            print("2. Extracting trending audio from audio/insta_link.txt...")
            if os.path.exists(insta_mp3):
                os.remove(insta_mp3)
            subprocess.run([
                "yt-dlp", "-x", "--audio-format", "mp3",
                "-o", os.path.join(AUDIO_DIR, "insta_bgm.%(ext)s"), url
            ], check=True)
            if os.path.exists(insta_mp3):
                return insta_mp3

    if os.path.exists(custom_bgm):
        print("2. Using audio/bgm.mp3...")
        return custom_bgm

    if ref_vid and os.path.exists(ref_vid):
        print("2. Extracting audio directly from Reference Reel...")
        VideoFileClip(ref_vid).audio.write_audiofile(insta_mp3, logger=None)
        return insta_mp3

    raise FileNotFoundError("References/style_link.txt ya audio/insta_link.txt mein link daalo!")

def build_insta_reel():
    target_w, target_h, bitrate, quality_label = get_export_resolution_and_bitrate(RAW_DIR)
    print(f"🎬 Insta Reel Export Mode: {quality_label} -> Canvas: {target_w}x{target_h} @ {bitrate}")

    ref_vid, cut_speed, ref_dur = fetch_reference_style()
    music_path = get_insta_audio(ref_vid)
    bgm = AudioFileClip(music_path)
    total_dur = min(bgm.duration, ref_dur if ref_vid else 30.0)
    bgm = bgm.subclip(0, total_dur)

    raws = sorted([
        f for f in glob.glob(os.path.join(RAW_DIR, "*.*"))
        if f.lower().endswith((".mp4", ".mov", ".mkv"))
    ])
    if not raws:
        raise FileNotFoundError("Raw video folder mein apni clips daalo!")

    print(f"3. Auto-editing {len(raws)} clips matching reference pacing ({cut_speed}s cuts) at {target_w}x{target_h}...")
    v_clips = []
    if len(raws) == 1:
        src = VideoFileClip(raws[0]).without_audio()
        num_cuts = max(5, int(total_dur // cut_speed))
        per_dur = total_dur / num_cuts
        starts = np.linspace(0, max(0.1, src.duration - per_dur), num_cuts)
        for idx, st in enumerate(starts):
            sub = src.subclip(st, min(st + per_dur, src.duration))
            v_clips.append(crop_and_pro_fx(sub, idx, target_w, target_h))
    else:
        per_dur = total_dur / len(raws)
        for idx, vp in enumerate(raws):
            c = VideoFileClip(vp).without_audio()
            if c.duration < per_dur:
                c = concatenate_videoclips([c] * (int(per_dur // c.duration) + 1))
            v_clips.append(crop_and_pro_fx(c.subclip(0, per_dur), idx, target_w, target_h))

    final_reel = concatenate_videoclips(v_clips, method="compose").set_audio(bgm)
    out_path = os.path.join(EXPORT_DIR, "insta_reel.mp4")
    final_reel.write_videofile(out_path, fps=30, codec="libx264", audio_codec="aac", bitrate=bitrate, preset="fast", threads=4)
    print(f"DONE! Reference-Styled Reel Ready at {target_w}x{target_h}: {out_path}")

if __name__ == "__main__":
    build_insta_reel()
