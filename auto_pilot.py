from audio_master import master_voice, mix_voice_and_bgm
import os, glob, json, shutil, asyncio, subprocess, requests, edge_tts
from build_video import build_video
from upload_youtube import upload_video

import sys
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
BASE_DIR = os.environ.get("WORKSPACE_DIR", SCRIPT_DIR)
RAW_DIR = os.path.join(BASE_DIR, "Raw video")
VIDEOS_DIR = os.path.join(BASE_DIR, "videos")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
os.makedirs(VIDEOS_DIR, exist_ok=True)
os.makedirs(RAW_DIR, exist_ok=True)

async def generate_ai_voice(text, audio_out, subs_out):
    communicate = edge_tts.Communicate(text, "hi-IN-MadhurNeural", rate="+12%", pitch="+2Hz")
    words = []
    with open(audio_out, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                st = chunk["offset"] / 10000000.0
                dur = chunk["duration"] / 10000000.0
                words.append({"word": chunk["text"], "start": st, "end": st + dur})
    grouped = [
        {
            "text": " ".join(x["word"] for x in words[i:i+3]),
            "start": words[i]["start"],
            "end": words[min(i+2, len(words)-1)]["end"]
        }
        for i in range(0, len(words), 3)
    ]
    json.dump(grouped, open(subs_out, "w", encoding="utf-8"), ensure_ascii=False)

def transcribe_my_voice(audio_out, subs_out):
    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_out, word_timestamps=True)
    words = []
    for seg in segments:
        for w in seg.words:
            words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
    grouped = [
        {
            "text": " ".join(x["word"] for x in words[i:i+3]),
            "start": words[i]["start"],
            "end": words[min(i+2, len(words)-1)]["end"]
        }
        for i in range(0, len(words), 3)
    ]
    json.dump(grouped, open(subs_out, "w", encoding="utf-8"), ensure_ascii=False)

def download_yt_links(links_file):
    links = [l.strip() for l in open(links_file, "r", encoding="utf-8") if l.strip().startswith("http")]
    if not links:
        return False
    for f in os.listdir(VIDEOS_DIR):
        os.remove(os.path.join(VIDEOS_DIR, f))
    for i, url in enumerate(links, 1):
        print(f"Downloading YouTube video {i}/{len(links)}: {url}...")
        out_tpl = os.path.join(VIDEOS_DIR, f"{i:02d}.mp4")
        subprocess.run([
            "yt-dlp", "-f", "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4", "-o", out_tpl, url
        ], check=True)
    return True

def fetch_wikimedia_video(query):
    headers = {"User-Agent": "YTShortsBot/1.0 (contact@example.com)"}
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"filetype:video {query}",
        "gsrnamespace": 6,
        "gsrlimit": 5,
        "prop": "videoinfo",
        "viprop": "url",
        "format": "json"
    }
    r = requests.get("https://commons.wikimedia.org/w/api.php", params=params, headers=headers, timeout=20).json()
    pages = list(r.get("query", {}).get("pages", {}).values())
    for p in pages:
        u = p.get("videoinfo", [{}])[0].get("url")
        if u and u.lower().endswith((".mp4", ".webm")):
            vr = requests.get(u, headers=headers, timeout=40)
            if len(vr.content) > 50000:
                return vr.content
    return None

def download_moving_videos(queries):
    for f in os.listdir(VIDEOS_DIR):
        os.remove(os.path.join(VIDEOS_DIR, f))
    for i, q in enumerate(queries, 1):
        print(f"Fetching HD moving clip {i}/{len(queries)}: {q}...")
        short_q = " ".join([w for w in q.replace(",", "").split() if len(w) > 2][:3])
        try:
            vid_bytes = fetch_wikimedia_video(short_q)
            if vid_bytes:
                with open(os.path.join(VIDEOS_DIR, f"{i:02d}.mp4"), "wb") as f:
                    f.write(vid_bytes)
        except Exception:
            pass

def run_autopilot():
    meta = json.load(open(os.path.join(BASE_DIR, "metadata.json"), "r", encoding="utf-8"))
    audio_out = os.path.join(AUDIO_DIR, "voiceover.mp3")
    subs_out = os.path.join(AUDIO_DIR, "subs.json")
    my_voices = glob.glob(os.path.join(AUDIO_DIR, "my_voice.*"))

    if my_voices:
        print("1. Using YOUR REAL VOICE + Auto-Generating Captions...")
        master_voice(my_voices[0], audio_out)
        transcribe_my_voice(audio_out, subs_out)
    else:
        print("1. Generating Energetic Voice + Captions...")
        raw_ai = os.path.join(AUDIO_DIR, "raw_ai.mp3")
        asyncio.run(generate_ai_voice(meta["script"], raw_ai, subs_out))
        master_voice(raw_ai, audio_out)

    links_file = os.path.join(RAW_DIR, "links.txt")
    raws = sorted([
        f for f in glob.glob(os.path.join(RAW_DIR, "*.*"))
        if f.lower().endswith((".mp4", ".mov", ".mkv"))
    ])

    if os.path.exists(links_file) and download_yt_links(links_file):
        print("2. Downloaded YouTube link(s) from Raw video/links.txt!")
    elif raws:
        for f in os.listdir(VIDEOS_DIR):
            os.remove(os.path.join(VIDEOS_DIR, f))
        for i, r in enumerate(raws, 1):
            shutil.copy(r, os.path.join(VIDEOS_DIR, f"{i:02d}.mp4"))
        print(f"2. Using {len(raws)} local clips from Raw video folder!")
    else:
        print("2. Fetching Free Moving HD Video Clips...")
        download_moving_videos(meta["prompts"])

    print("3. Rendering Pro Studio Master Edit...")
    build_video()
    print("4. Uploading to YouTube Shorts...")
    upload_video()
    print("DONE! Video Uploaded & Saved in Export/final_video.mp4!")

if __name__ == "__main__":
    run_autopilot()