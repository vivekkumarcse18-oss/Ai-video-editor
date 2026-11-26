import os, glob, json
import numpy as np
import cv2
import PIL.Image
if not hasattr(PIL.Image, "ANTIALIAS"):
    PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS
from PIL import Image, ImageDraw, ImageFont
try:
    from moviepy.editor import VideoFileClip, AudioFileClip, ImageClip, CompositeVideoClip, CompositeAudioClip, concatenate_videoclips
    from moviepy.audio.AudioClip import AudioArrayClip
except (ImportError, ModuleNotFoundError):
    try:
        from moviepy import VideoFileClip, AudioFileClip, ImageClip, CompositeVideoClip, CompositeAudioClip, concatenate_videoclips
        from moviepy.audio.AudioClip import AudioArrayClip
    except Exception:
        from moviepy.video.io.VideoFileClip import VideoFileClip
        from moviepy.audio.io.AudioFileClip import AudioFileClip
        from moviepy.video.VideoClip import ImageClip
        from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
        from moviepy.audio.AudioClip import CompositeAudioClip, AudioArrayClip
        from moviepy.video.compositing.concatenate import concatenate_videoclips

import sys
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
BASE_DIR = os.environ.get("WORKSPACE_DIR", SCRIPT_DIR)

# Resolution & Quality Detector
def get_export_resolution_and_bitrate(raw_dir=None):
    mode = os.environ.get("EXPORT_QUALITY", "auto").strip().lower()
    if mode == "4k":
        return 2160, 3840, "35000k", "4K Ultra HD (Forced 2160x3840 @ 35 Mbps)"
    elif mode == "1080p":
        return 1080, 1920, "14000k", "1080p Full HD (Forced 1080x1920 @ 14 Mbps)"

    # Auto mode: inspect clips in Raw video/
    if not raw_dir:
        raw_dir = os.path.join(BASE_DIR, "Raw video")

    is_4k = False
    clips = []
    if os.path.exists(raw_dir):
        clips.extend([
            os.path.join(raw_dir, f) for f in os.listdir(raw_dir)
            if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm"))
        ])
    if not clips:
        vids_dir = os.path.join(BASE_DIR, "videos")
        if os.path.exists(vids_dir):
            clips.extend([
                os.path.join(vids_dir, f) for f in os.listdir(vids_dir)
                if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm"))
            ])

    for c in clips:
        try:
            cap = cv2.VideoCapture(c)
            if cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                cap.release()
                if w >= 2160 or h >= 2160:
                    is_4k = True
                    break
        except Exception:
            pass

    if is_4k:
        return 2160, 3840, "35000k", "Auto (4K Detected: 2160x3840 @ 35 Mbps)"
    else:
        return 1080, 1920, "14000k", "Auto (1080p Full HD: 1080x1920 @ 14 Mbps)"

# Dynamic Cached Vignette for any resolution
_VIGNETTE_CACHE = {}

def get_vignette(w, h):
    key = (w, h)
    if key not in _VIGNETTE_CACHE:
        X, Y = np.meshgrid(np.linspace(-1, 1, w), np.linspace(-1, 1, h))
        _VIGNETTE_CACHE[key] = np.clip(1.0 - 0.30 * (X**2 + Y**2), 0.58, 1.0)[:, :, None].astype(np.float32)
    return _VIGNETTE_CACHE[key]

def apply_davinci_grade(frame):
    img = frame.astype(np.float32) / 255.0
    img = np.clip((img - 0.5) * 1.15 + 0.52, 0.0, 1.0)
    hsv = cv2.cvtColor((img * 255).astype(np.uint8), cv2.COLOR_RGB2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.20, 0, 255)
    graded = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB).astype(np.float32)
    shadows = graded < 110
    highlights = graded > 160
    graded[:, :, 2][shadows[:, :, 2]] = np.clip(graded[:, :, 2][shadows[:, :, 2]] + 8, 0, 255)
    graded[:, :, 0][highlights[:, :, 0]] = np.clip(graded[:, :, 0][highlights[:, :, 0]] + 7, 0, 255)
    h, w = frame.shape[:2]
    vignette = get_vignette(w, h)
    return np.clip(graded * vignette, 0, 255).astype(np.uint8)

def make_whoosh_sfx(dur=0.28, sr=44100):
    t = np.linspace(0, dur, int(sr * dur))
    env = np.sin(np.pi * (t / dur)) ** 2
    noise = np.random.normal(0, 1, len(t))
    smooth = np.convolve(noise, np.ones(18)/18.0, mode="same") * env * 0.35
    return AudioArrayClip(np.column_stack([smooth, smooth]).astype(np.float32), fps=sr)

def make_tension_bed(dur, sr=44100):
    t = np.linspace(0, dur, int(sr * dur))
    pulse = 0.6 + 0.4 * np.sin(2 * np.pi * 1.5 * t)
    bass = (np.sin(2 * np.pi * 55 * t) + 0.5 * np.sin(2 * np.pi * 82.4 * t)) * pulse * 0.045
    return AudioArrayClip(np.column_stack([bass, bass]).astype(np.float32), fps=sr)

def crop_and_pro_fx(clip, mode=0, target_w=1080, target_h=1920):
    w, h = clip.size
    target_ratio = target_w / target_h
    if (w / h) > target_ratio:
        new_w = int(h * target_ratio)
        x1 = (w - new_w) // 2
        clip = clip.crop(x1=x1, y1=0, x2=x1+new_w, y2=h)
    else:
        new_h = int(w / target_ratio)
        y1 = (h - new_h) // 2
        clip = clip.crop(x1=0, y1=y1, x2=w, y2=y1+new_h)

    int_w = int(target_w * 1.14815)
    int_h = int(target_h * 1.14815)
    clip = clip.resize((int_w, int_h))
    dur = max(clip.duration, 0.1)
    scale_factor = target_w / 1080.0

    def fx(get_frame, t):
        frm = apply_davinci_grade(get_frame(t))
        if t < 0.10:
            flash = int((1.0 - t / 0.10) * 55)
            frm = np.clip(frm.astype(np.int16) + flash, 0, 255).astype(np.uint8)
        p = t / dur
        trans_punch = 0.12 * max(0.0, 1.0 - (t / 0.25)) if t < 0.25 else 0.0
        scale = (1.0 + 0.08 * p + trans_punch) if mode % 2 == 0 else (1.08 - 0.08 * p + trans_punch)
        cw, ch = int(target_w / scale), int(target_h / scale)
        cx, cy = int_w // 2, int_h // 2
        if t < 0.20:
            shift = int((1.0 - t / 0.20) * (75 * scale_factor)) * (1 if mode % 2 == 0 else -1)
            cx = max(cw//2, min(int_w - cw//2, cx + shift))
        crop = frm[cy - ch//2 : cy + ch//2, cx - cw//2 : cx + cw//2]
        return cv2.resize(crop, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    return clip.fl(fx)

def prep_clip(v_path, target_dur, idx=0, target_w=1080, target_h=1920):
    clip = VideoFileClip(v_path).without_audio()
    if clip.duration < target_dur:
        clip = concatenate_videoclips([clip] * (int(target_dur // clip.duration) + 1))
    return crop_and_pro_fx(clip.subclip(0, target_dur), idx, target_w, target_h)

def auto_trim_long_video(v_path, total_dur, target_w=1080, target_h=1920):
    src = VideoFileClip(v_path).without_audio()
    num_cuts = max(6, int(total_dur // 3))
    per_dur = total_dur / num_cuts
    usable_start = min(5.0, src.duration * 0.1)
    usable_end = max(usable_start + per_dur, src.duration * 0.9 - per_dur)
    starts = np.linspace(usable_start, usable_end, num_cuts)
    cuts = []
    for idx, st in enumerate(starts):
        end_t = min(st + per_dur, src.duration)
        sub = src.subclip(st, end_t)
        if sub.duration < per_dur:
            sub = concatenate_videoclips([sub] * (int(per_dur // max(sub.duration, 0.1)) + 1)).subclip(0, per_dur)
        cuts.append(crop_and_pro_fx(sub, idx, target_w, target_h))
    return cuts

def make_hook_banner(title_txt, total_dur, target_w=1080, target_h=1920):
    scale_factor = target_w / 1080.0
    clean_title = title_txt.split("#")[0].strip()[:34].upper()
    if not clean_title:
        clean_title = "MUST WATCH"
    font_size = int(54 * scale_factor)
    try:
        font = ImageFont.truetype("impact.ttf", font_size)
    except Exception:
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

    box_h = int(160 * scale_factor)
    img = Image.new("RGBA", (target_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), clean_title, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x, y = (target_w - tw) // 2, (box_h - th) // 2
    pad_x = int(32 * scale_factor)
    pad_y = int(16 * scale_factor)
    radius = int(20 * scale_factor)
    outline_w = int(4 * scale_factor)
    draw.rounded_rectangle(
        [x - pad_x, y - pad_y, x + tw + pad_x, y + th + int(20 * scale_factor)],
        radius=radius,
        fill=(220, 20, 40, 235),
        outline=(255, 255, 255, 255),
        width=outline_w
    )
    draw.text((x, y), clean_title, font=font, fill=(255, 255, 255, 255))
    y_pos = int(170 * scale_factor)
    return ImageClip(np.array(img)).set_start(0).set_duration(min(4.0, total_dur)).set_position(("center", y_pos))

def make_caption_clips(subs, target_w=1080, target_h=1920):
    scale_factor = target_w / 1080.0
    clips = []
    font_size = int(82 * scale_factor)
    try:
        font = ImageFont.truetype("impact.ttf", font_size)
    except Exception:
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

    box_h = int(300 * scale_factor)
    pad_x = int(30 * scale_factor)
    pad_y = int(18 * scale_factor)
    radius = int(24 * scale_factor)
    shadow_offset = max(2, int(4 * scale_factor))
    y_pos = int(1310 * scale_factor)

    for s in subs:
        txt = s["text"].upper()
        dur = max(0.15, s["end"] - s["start"])
        img = Image.new("RGBA", (target_w, box_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), txt, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x, y = (target_w - tw) // 2, (box_h - th) // 2
        draw.rounded_rectangle(
            [x - pad_x, y - pad_y, x + tw + pad_x, y + th + int(24 * scale_factor)],
            radius=radius,
            fill=(0, 0, 0, 210)
        )
        draw.text((x + shadow_offset, y + shadow_offset), txt, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), txt, font=font, fill=(255, 230, 0, 255))
        c = ImageClip(np.array(img)).set_start(s["start"]).set_duration(dur)
        c = c.set_position(("center", y_pos))
        clips.append(c)
    return clips

def make_progress_bar(total_dur, target_w=1080, target_h=1920):
    scale_factor = target_w / 1080.0
    bar_h = max(14, int(14 * scale_factor))
    bar_arr = np.zeros((bar_h, target_w, 3), dtype=np.uint8)
    bar_arr[:, :] = [255, 45, 45]
    base = ImageClip(bar_arr).set_duration(total_dur).set_position((0, 0))
    def bar_fx(get_frame, t):
        frm = get_frame(t).copy()
        fill_w = int(target_w * (t / max(total_dur, 0.1)))
        frm[:, fill_w:] = [40, 40, 40]
        return frm
    return base.fl(bar_fx)

def build_video():
    target_w, target_h, bitrate, quality_label = get_export_resolution_and_bitrate()
    print(f"🎬 Studio Master Export Mode: {quality_label} -> Canvas: {target_w}x{target_h} @ {bitrate}")

    audio_path = os.path.join(BASE_DIR, "audio", "voiceover.mp3")
    bgm_path = os.path.join(BASE_DIR, "audio", "bgm.mp3")
    subs_path = os.path.join(BASE_DIR, "audio", "subs.json")
    meta_path = os.path.join(BASE_DIR, "metadata.json")
    out_path = os.path.join(BASE_DIR, "Export", "final_video.mp4")
    voice = AudioFileClip(audio_path)
    total_dur = voice.duration
    vids = sorted(glob.glob(os.path.join(BASE_DIR, "videos", "*.mp4")))
    if len(vids) == 1:
        v_clips = auto_trim_long_video(vids[0], total_dur, target_w, target_h)
    else:
        per_dur = total_dur / max(1, len(vids))
        v_clips = [prep_clip(vp, per_dur, i, target_w, target_h) for i, vp in enumerate(vids)]
    audio_tracks = [voice]
    whoosh = make_whoosh_sfx()
    cur_t = 0.0
    for vc in v_clips[:-1]:
        cur_t += vc.duration
        if cur_t - 0.14 > 0:
            audio_tracks.append(whoosh.set_start(cur_t - 0.14))
    if os.path.exists(bgm_path):
        bgm = AudioFileClip(bgm_path).volumex(0.12)
        if bgm.duration < total_dur:
            bgm = concatenate_videoclips([bgm] * (int(total_dur // bgm.duration) + 1))
        audio_tracks.append(bgm.subclip(0, total_dur))
    else:
        audio_tracks.append(make_tension_bed(total_dur))
    final_audio = CompositeAudioClip(audio_tracks)
    video = concatenate_videoclips(v_clips, method="compose").set_audio(final_audio)
    layers = [video, make_progress_bar(total_dur, target_w, target_h)]
    if os.path.exists(meta_path):
        meta = json.load(open(meta_path, "r", encoding="utf-8"))
        layers.append(make_hook_banner(meta.get("title", ""), total_dur, target_w, target_h))
    if os.path.exists(subs_path):
        subs = json.load(open(subs_path, "r", encoding="utf-8"))
        layers.extend(make_caption_clips(subs, target_w, target_h))
    final_vid = CompositeVideoClip(layers, size=(target_w, target_h)).set_duration(total_dur)
    if final_vid.audio:
        final_vid = final_vid.set_audio(final_vid.audio.set_duration(total_dur))
    final_vid.write_videofile(out_path, fps=30, codec="libx264", audio_codec="aac", bitrate=bitrate, preset="fast", threads=4)
    print(f"STUDIO MASTER EDIT READY AT {target_w}x{target_h}: {out_path}")

if __name__ == "__main__":
    build_video()
