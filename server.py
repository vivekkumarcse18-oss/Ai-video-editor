import os
import sys
import json
import time
import glob
import re
import queue
import shutil
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version, PackageNotFoundError
from flask import Flask, request, jsonify, Response, send_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACES_ROOT = os.path.join(BASE_DIR, "workspaces")
os.makedirs(WORKSPACES_ROOT, exist_ok=True)

# Ensure base folders exist for local fallback
for f in ["Raw video", "References", "Refrences", "audio", "Export", "videos"]:
    os.makedirs(os.path.join(BASE_DIR, f), exist_ok=True)

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")

# --- Multi-Session Workspace Resolver ---
def get_session_info():
    """
    Resolves session_id from Header 'X-Session-ID', Query 'session_id', or JSON body.
    Returns (clean_session_id, workspace_directory_path).
    If no session_id is provided, returns ('default', BASE_DIR) for local compatibility.
    """
    session_id = request.headers.get("X-Session-ID") or request.args.get("session_id")
    if not session_id and request.is_json:
        session_id = (request.get_json(silent=True) or {}).get("session_id")

    if session_id:
        clean_id = re.sub(r'[^a-zA-Z0-9_\-]', '', str(session_id))[:64]
        if clean_id and clean_id != "default":
            ws_dir = os.path.join(WORKSPACES_ROOT, clean_id)
            for sub in ["Raw video", "References", "Refrences", "audio", "Export", "videos"]:
                os.makedirs(os.path.join(ws_dir, sub), exist_ok=True)

            meta_path = os.path.join(ws_dir, "metadata.json")
            if not os.path.exists(meta_path):
                root_meta = os.path.join(BASE_DIR, "metadata.json")
                if os.path.exists(root_meta):
                    try:
                        shutil.copy2(root_meta, meta_path)
                    except Exception:
                        pass
                if not os.path.exists(meta_path):
                    with open(meta_path, "w", encoding="utf-8") as mf:
                        json.dump({
                            "title": "My Awesome Video",
                            "description": "Created with AI Video Studio",
                            "tags": ["shorts", "viral"],
                            "script": "",
                            "prompts": []
                        }, mf, indent=2)

            # Touch workspace to keep mtime fresh
            try:
                os.utime(ws_dir, None)
            except Exception:
                pass

            return clean_id, ws_dir

    return "default", BASE_DIR

def get_workspace_paths(ws_dir):
    return {
        "raw": os.path.join(ws_dir, "Raw video"),
        "ref": os.path.join(ws_dir, "References"),
        "alt_ref": os.path.join(ws_dir, "Refrences"),
        "audio": os.path.join(ws_dir, "audio"),
        "export": os.path.join(ws_dir, "Export"),
        "videos": os.path.join(ws_dir, "videos"),
        "meta": os.path.join(ws_dir, "metadata.json")
    }

# --- Multi-Session Concurrent Job Queue Manager ---
class MultiSessionQueueManager:
    def __init__(self, max_workers=3):
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="StudioWorker")
        self.max_workers = max_workers
        self.sessions = {}  # session_id -> dict state
        self.waiting_queue = []  # list of session_ids waiting for worker execution
        self.active_workers_count = 0

    def _ensure_session(self, session_id):
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "status": "idle",       # idle | queued | running | done | error
                "engine": None,
                "script": None,
                "process": None,
                "logs": [],
                "subscribers": [],
                "start_time": 0,
                "quality": "auto"
            }
        return self.sessions[session_id]

    def is_session_active(self, session_id):
        with self.lock:
            state = self.sessions.get(session_id)
            if not state:
                return False
            return state["status"] in ["queued", "running"]

    def subscribe(self, session_id):
        q = queue.Queue(maxsize=1000)
        with self.lock:
            state = self._ensure_session(session_id)
            for item in state["logs"][-200:]:
                q.put_nowait(item)
            state["subscribers"].append(q)
        return q

    def unsubscribe(self, session_id, q):
        with self.lock:
            state = self.sessions.get(session_id)
            if state and q in state["subscribers"]:
                state["subscribers"].remove(q)

    def broadcast(self, session_id, data):
        with self.lock:
            state = self._ensure_session(session_id)
            state["logs"].append(data)
            if len(state["logs"]) > 2000:
                state["logs"].pop(0)

            dead_subs = []
            for q in state["subscribers"]:
                try:
                    q.put_nowait(data)
                except queue.Full:
                    dead_subs.append(q)
            for q in dead_subs:
                state["subscribers"].remove(q)

    def get_queue_position(self, session_id):
        with self.lock:
            if session_id in self.waiting_queue:
                return self.waiting_queue.index(session_id) + 1
            state = self.sessions.get(session_id)
            if state and state["status"] == "running":
                return 0
            return -1

    def submit_job(self, session_id, ws_dir, engine_name, script_name, quality="auto"):
        with self.lock:
            state = self._ensure_session(session_id)
            if state["status"] in ["queued", "running"]:
                return False, f"Job already {state['status']} for this session. Please wait or stop."

            state["status"] = "queued"
            state["engine"] = engine_name
            state["script"] = script_name
            state["quality"] = quality
            state["start_time"] = time.time()
            state["logs"].clear()
            self.waiting_queue.append(session_id)
            queue_pos = len(self.waiting_queue)

        self.broadcast(session_id, {
            "type": "queued",
            "engine": engine_name,
            "position": queue_pos,
            "message": f"⏳ Job queued at position #{queue_pos} (Cloud render worker pool: {self.max_workers} concurrent)...",
            "timestamp": time.strftime("%H:%M:%S")
        })

        def run_task():
            with self.lock:
                if session_id in self.waiting_queue:
                    self.waiting_queue.remove(session_id)
                state["status"] = "running"
                self.active_workers_count += 1

            self.broadcast(session_id, {
                "type": "start",
                "engine": engine_name,
                "message": f"🚀 Starting Engine: {engine_name} ({script_name}) [Quality: {quality.upper()}]...",
                "timestamp": time.strftime("%H:%M:%S")
            })

            script_path = os.path.join(BASE_DIR, script_name)
            cmd = [sys.executable, "-u", script_path]

            proc_env = os.environ.copy()
            proc_env["WORKSPACE_DIR"] = ws_dir
            proc_env["EXPORT_QUALITY"] = quality

            rc = 0
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                    cwd=BASE_DIR,
                    encoding="utf-8",
                    errors="replace",
                    env=proc_env
                )
                with self.lock:
                    state["process"] = proc

                for line in iter(proc.stdout.readline, ''):
                    if not line:
                        break
                    clean_line = line.rstrip()
                    if clean_line:
                        self.broadcast(session_id, {
                            "type": "log",
                            "engine": engine_name,
                            "line": clean_line,
                            "timestamp": time.strftime("%H:%M:%S")
                        })

                proc.stdout.close()
                rc = proc.wait()
            except Exception as e:
                rc = -1
                self.broadcast(session_id, {
                    "type": "error",
                    "engine": engine_name,
                    "line": f"Execution error: {str(e)}",
                    "timestamp": time.strftime("%H:%M:%S")
                })
            finally:
                with self.lock:
                    state["process"] = None
                    state["status"] = "done" if rc == 0 else "error"
                    self.active_workers_count = max(0, self.active_workers_count - 1)

                status_msg = "✅ Completed successfully!" if rc == 0 else f"❌ Process terminated with exit code {rc}"
                self.broadcast(session_id, {
                    "type": "end",
                    "engine": engine_name,
                    "code": rc,
                    "message": status_msg,
                    "timestamp": time.strftime("%H:%M:%S")
                })

        self.executor.submit(run_task)
        return True, f"Queued at position #{queue_pos}"

    def stop_job(self, session_id):
        with self.lock:
            state = self.sessions.get(session_id)
            if not state or state["status"] not in ["queued", "running"]:
                return False, "No active job running for this session."

            if state["status"] == "queued" and session_id in self.waiting_queue:
                self.waiting_queue.remove(session_id)
                state["status"] = "idle"
                self.broadcast(session_id, {
                    "type": "end",
                    "engine": state["engine"],
                    "code": -999,
                    "message": "⏹️ Job removed from queue by user.",
                    "timestamp": time.strftime("%H:%M:%S")
                })
                return True, "Job removed from queue."

            proc = state.get("process")
            if proc:
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
                    else:
                        proc.terminate()
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                state["status"] = "idle"
                state["process"] = None
                self.broadcast(session_id, {
                    "type": "end",
                    "engine": state["engine"],
                    "code": -999,
                    "message": "⏹️ Process stopped by user.",
                    "timestamp": time.strftime("%H:%M:%S")
                })
                return True, "Process stopped."

            return False, "Process could not be stopped."

    def get_status(self, session_id):
        with self.lock:
            state = self.sessions.get(session_id)
            if not state:
                return {
                    "status": "idle",
                    "is_running": False,
                    "current_engine": None,
                    "queue_position": -1,
                    "uptime": 0,
                    "active_workers": self.active_workers_count,
                    "max_workers": self.max_workers
                }
            is_running = state["status"] in ["queued", "running"]
            q_pos = self.waiting_queue.index(session_id) + 1 if session_id in self.waiting_queue else (0 if state["status"] == "running" else -1)
            uptime = round(time.time() - state["start_time"], 1) if is_running else 0
            return {
                "status": state["status"],
                "is_running": is_running,
                "current_engine": state["engine"],
                "queue_position": q_pos,
                "uptime": uptime,
                "active_workers": self.active_workers_count,
                "max_workers": self.max_workers
            }

queue_manager = MultiSessionQueueManager(max_workers=3)

# --- Background Cleanup Thread (Workspaces older than 2 hours) ---
def cleanup_workspaces_worker():
    while True:
        try:
            time.sleep(600)  # Every 10 minutes
            if os.path.exists(WORKSPACES_ROOT):
                now = time.time()
                for entry in os.listdir(WORKSPACES_ROOT):
                    entry_path = os.path.join(WORKSPACES_ROOT, entry)
                    if os.path.isdir(entry_path):
                        if queue_manager.is_session_active(entry):
                            continue
                        try:
                            mtime = os.path.getmtime(entry_path)
                            if (now - mtime) > 7200:  # 2 hours
                                shutil.rmtree(entry_path, ignore_errors=True)
                                print(f"[Cleanup] Deleted expired workspace: {entry}")
                        except Exception as e:
                            print(f"[Cleanup] Error processing {entry}: {e}")
        except Exception as e:
            print(f"[Cleanup] Thread exception: {e}")

cleanup_thread = threading.Thread(target=cleanup_workspaces_worker, daemon=True)
cleanup_thread.start()

# --- HTTP Range Streaming Helper for Videos ---
def send_file_partial(file_path):
    if not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404

    size = os.path.getsize(file_path)
    range_header = request.headers.get('Range', None)

    if not range_header:
        return send_file(file_path, mimetype='video/mp4', as_attachment=False)

    byte_range = re.search(r'bytes=(\d+)-(\d*)', range_header)
    if not byte_range:
        return send_file(file_path, mimetype='video/mp4')

    start = int(byte_range.group(1))
    end = int(byte_range.group(2)) if byte_range.group(2) else size - 1

    if start >= size:
        return Response("Requested range not satisfiable", status=416, headers={'Content-Range': f'bytes */{size}'})

    length = end - start + 1

    def generate_chunks():
        with open(file_path, 'rb') as f:
            f.seek(start)
            remaining = length
            chunk_size = 1024 * 512
            while remaining > 0:
                read_bytes = min(remaining, chunk_size)
                data = f.read(read_bytes)
                if not data:
                    break
                remaining -= len(data)
                yield data

    response = Response(generate_chunks(), status=206, mimetype='video/mp4')
    response.headers['Content-Range'] = f'bytes {start}-{end}/{size}'
    response.headers['Accept-Ranges'] = 'bytes'
    response.headers['Content-Length'] = str(length)
    return response

# --- API Endpoints ---

@app.route("/")
def serve_index():
    index_file = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return f.read(), 200, {
                "Content-Type": "text/html; charset=utf-8",
                "Cache-Control": "no-cache, no-store, must-revalidate"
            }
    return "<h1>Local Video Studio</h1><p>index.html not found</p>", 404

@app.route("/api/session/init", methods=["GET"])
def session_init():
    session_id, ws_dir = get_session_info()
    return jsonify({
        "status": "success",
        "session_id": session_id,
        "workspace": ws_dir,
        "is_isolated": session_id != "default"
    })

@app.route("/api/setup", methods=["GET"])
def check_setup():
    required_pkgs = [
        "flask", "fastapi", "uvicorn", "yt-dlp", "edge-tts",
        "faster-whisper", "moviepy", "opencv-python", "pillow",
        "google-api-python-client", "google-auth-oauthlib", "pywebview", "pyinstaller"
    ]
    pkg_status = {}
    for pkg in required_pkgs:
        norm_name = pkg.replace("-", "_")
        installed = False
        ver = None
        for test_name in [pkg, norm_name, pkg.lower(), norm_name.lower()]:
            try:
                ver = version(test_name)
                installed = True
                break
            except PackageNotFoundError:
                continue
        pkg_status[pkg] = {
            "installed": installed,
            "version": ver if installed else "Not installed"
        }

    # Check FFmpeg
    ffmpeg_installed = False
    ffmpeg_path = ""
    try:
        check_cmd = ["where", "ffmpeg"] if os.name == "nt" else ["which", "ffmpeg"]
        res = subprocess.run(check_cmd, capture_output=True, text=True)
        if res.returncode == 0:
            ffmpeg_installed = True
            ffmpeg_path = res.stdout.strip().splitlines()[0]
    except Exception:
        pass

    return jsonify({
        "status": "ok",
        "packages": pkg_status,
        "ffmpeg": {
            "installed": ffmpeg_installed,
            "path": ffmpeg_path or "FFmpeg not detected in PATH"
        }
    })

@app.route("/api/setup/install", methods=["POST"])
def install_package():
    data = request.get_json(silent=True) or {}
    pkg_name = data.get("package")
    if not pkg_name:
        return jsonify({"error": "No package specified"}), 400

    try:
        res = subprocess.run([sys.executable, "-m", "pip", "install", pkg_name], capture_output=True, text=True)
        return jsonify({
            "status": "success" if res.returncode == 0 else "error",
            "code": res.returncode,
            "output": res.stdout + res.stderr
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/links", methods=["GET", "POST"])
def manage_links():
    session_id, ws_dir = get_session_info()
    paths = get_workspace_paths(ws_dir)

    raw_links_path = os.path.join(paths["raw"], "links.txt")
    insta_links_path = os.path.join(paths["audio"], "insta_link.txt")
    ref_links_path = os.path.join(paths["ref"], "style_link.txt")
    alt_ref_links_path = os.path.join(paths["alt_ref"], "style_link.txt")

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        if "yt_links" in data:
            with open(raw_links_path, "w", encoding="utf-8") as f:
                f.write(data["yt_links"].strip() + "\n")
        if "insta_link" in data:
            with open(insta_links_path, "w", encoding="utf-8") as f:
                f.write(data["insta_link"].strip() + "\n")
        if "ref_link" in data:
            val = data["ref_link"].strip() + "\n"
            with open(ref_links_path, "w", encoding="utf-8") as f:
                f.write(val)
            try:
                with open(alt_ref_links_path, "w", encoding="utf-8") as f:
                    f.write(val)
            except Exception:
                pass

        return jsonify({"status": "success", "message": "Links saved successfully", "session_id": session_id})

    # GET
    def read_safe(p):
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
        return ""

    ref_val = read_safe(ref_links_path) or read_safe(alt_ref_links_path)

    return jsonify({
        "yt_links": read_safe(raw_links_path),
        "insta_link": read_safe(insta_links_path),
        "ref_link": ref_val,
        "session_id": session_id
    })

@app.route("/api/links/clear", methods=["POST"])
def clear_links():
    session_id, ws_dir = get_session_info()
    paths = get_workspace_paths(ws_dir)
    data = request.get_json(silent=True) or {}
    clear_type = data.get("type", "all")

    targets = []
    if clear_type in ["yt", "all"]:
        targets.append(os.path.join(paths["raw"], "links.txt"))
    if clear_type in ["insta", "all"]:
        targets.append(os.path.join(paths["audio"], "insta_link.txt"))
    if clear_type in ["ref", "all"]:
        targets.extend([
            os.path.join(paths["ref"], "style_link.txt"),
            os.path.join(paths["alt_ref"], "style_link.txt")
        ])

    for p in targets:
        if os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write("")
    return jsonify({"status": "success", "message": f"Cleared links ({clear_type}).", "session_id": session_id})

@app.route("/api/upload/<category>", methods=["POST"])
def upload_file(category):
    session_id, ws_dir = get_session_info()
    paths = get_workspace_paths(ws_dir)

    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded in form data 'file'"}), 400

    uploaded = request.files['file']
    filename = uploaded.filename
    if not filename:
        return jsonify({"error": "Empty filename"}), 400

    clean_filename = os.path.basename(filename)

    if category == "raw":
        dest = os.path.join(paths["raw"], clean_filename)
        uploaded.save(dest)
        return jsonify({"status": "success", "filename": clean_filename, "path": "Raw video/" + clean_filename, "session_id": session_id})

    elif category == "voice":
        ext = os.path.splitext(clean_filename)[1].lower() or ".mp3"
        dest = os.path.join(paths["audio"], "my_voice" + ext)
        uploaded.save(dest)
        return jsonify({"status": "success", "filename": "my_voice" + ext, "path": "audio/" + os.path.basename(dest), "session_id": session_id})

    elif category == "bgm":
        dest = os.path.join(paths["audio"], "bgm.mp3")
        uploaded.save(dest)
        return jsonify({"status": "success", "filename": "bgm.mp3", "path": "audio/bgm.mp3", "session_id": session_id})

    elif category == "reference":
        dest = os.path.join(paths["ref"], "ref_sample.mp4")
        uploaded.save(dest)
        try:
            shutil.copy2(dest, os.path.join(paths["alt_ref"], "ref_sample.mp4"))
        except Exception:
            pass
        return jsonify({"status": "success", "filename": "ref_sample.mp4", "path": "References/ref_sample.mp4", "session_id": session_id})

    return jsonify({"error": "Invalid upload category"}), 400

@app.route("/api/files", methods=["GET"])
def list_files():
    session_id, ws_dir = get_session_info()
    paths = get_workspace_paths(ws_dir)

    def get_dir_files(folder, relative_name):
        res = []
        if os.path.exists(folder):
            for f in os.listdir(folder):
                full_path = os.path.join(folder, f)
                if os.path.isfile(full_path):
                    st = os.stat(full_path)
                    res.append({
                        "name": f,
                        "relative": f"{relative_name}/{f}",
                        "size": st.st_size,
                        "size_mb": round(st.st_size / (1024 * 1024), 2),
                        "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
                    })
        return res

    return jsonify({
        "session_id": session_id,
        "raw_video": get_dir_files(paths["raw"], "Raw video"),
        "audio": get_dir_files(paths["audio"], "audio"),
        "references": get_dir_files(paths["ref"], "References"),
        "export": get_dir_files(paths["export"], "Export"),
        "videos": get_dir_files(paths["videos"], "videos")
    })

@app.route("/api/files/<folder_key>/<filename>", methods=["DELETE"])
def delete_file(folder_key, filename):
    session_id, ws_dir = get_session_info()
    paths = get_workspace_paths(ws_dir)

    folder_map = {
        "raw": paths["raw"],
        "raw video": paths["raw"],
        "raw_video": paths["raw"],
        "audio": paths["audio"],
        "references": paths["ref"],
        "refrences": paths["alt_ref"],
        "videos": paths["videos"],
        "export": paths["export"]
    }
    target_folder = folder_map.get(folder_key.lower())
    if not target_folder:
        return jsonify({"error": "Invalid folder"}), 400

    safe_name = os.path.basename(filename)
    target_path = os.path.join(target_folder, safe_name)

    if os.path.exists(target_path):
        os.remove(target_path)
        return jsonify({"status": "success", "message": f"Deleted {safe_name}", "session_id": session_id})
    return jsonify({"error": "File not found"}), 404

@app.route("/api/metadata", methods=["GET", "POST"])
def handle_metadata():
    session_id, ws_dir = get_session_info()
    meta_path = os.path.join(ws_dir, "metadata.json")

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({"status": "success", "message": "metadata.json updated", "session_id": session_id})

    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return jsonify(json.load(f))
        except Exception as e:
            return jsonify({"error": f"Error reading metadata.json: {str(e)}"}), 500
    return jsonify({
        "title": "My Awesome Video",
        "description": "Created with AI Video Studio",
        "tags": ["shorts", "viral"],
        "script": "",
        "prompts": []
    })

@app.route("/api/execute/<engine>", methods=["POST"])
def execute_engine(engine):
    session_id, ws_dir = get_session_info()
    paths = get_workspace_paths(ws_dir)

    engine_map = {
        "auto_pilot": ("YT Shorts Auto-Pilot", "auto_pilot.py"),
        "vlog_pro": ("Pro Self-Vlog Editor", "vlog_pro.py"),
        "insta_reel": ("Insta Reel Style Matcher", "insta_reel.py"),
        "build_video": ("Video Builder", "build_video.py"),
        "upload_youtube": ("YouTube Uploader", "upload_youtube.py")
    }

    if engine not in engine_map:
        return jsonify({"error": f"Unknown engine: {engine}"}), 400

    payload = request.get_json(silent=True) or {}
    quality = payload.get("quality", "auto").strip().lower()
    if quality not in ["auto", "1080p", "4k"]:
        quality = "auto"

    # If uploading to YouTube, prepare selected video in session export directory
    if engine == "upload_youtube":
        selected_vid = payload.get("video")
        if selected_vid:
            src_vid = os.path.join(paths["export"], os.path.basename(selected_vid))
            dest_vid = os.path.join(paths["export"], "final_video.mp4")
            if os.path.exists(src_vid) and src_vid != dest_vid:
                shutil.copy2(src_vid, dest_vid)

        # Update metadata if fields provided
        if any(k in payload for k in ["title", "description", "tags", "privacyStatus", "categoryId"]):
            current_meta = {}
            if os.path.exists(paths["meta"]):
                try:
                    with open(paths["meta"], "r", encoding="utf-8") as f:
                        current_meta = json.load(f)
                except Exception:
                    pass
            for k in ["title", "description", "tags", "privacyStatus", "categoryId"]:
                if k in payload:
                    current_meta[k] = payload[k]
            with open(paths["meta"], "w", encoding="utf-8") as f:
                json.dump(current_meta, f, ensure_ascii=False, indent=2)

    name, script = engine_map[engine]
    success, msg = queue_manager.submit_job(
        session_id=session_id,
        ws_dir=ws_dir,
        engine_name=name,
        script_name=script,
        quality=quality
    )

    if not success:
        return jsonify({"error": msg, "session_id": session_id}), 409

    return jsonify({
        "status": "queued",
        "engine": name,
        "script": script,
        "message": msg,
        "session_id": session_id
    })

@app.route("/api/stop", methods=["POST"])
def stop_engine():
    session_id, _ = get_session_info()
    success, msg = queue_manager.stop_job(session_id)
    return jsonify({"status": "stopped" if success else "error", "message": msg, "session_id": session_id})

@app.route("/api/status", methods=["GET"])
def engine_status():
    session_id, _ = get_session_info()
    info = queue_manager.get_status(session_id)
    info["session_id"] = session_id
    return jsonify(info)

@app.route("/api/stream-logs", methods=["GET"])
def stream_logs():
    session_id, _ = get_session_info()
    q = queue_manager.subscribe(session_id)

    def generate_events():
        try:
            status_data = queue_manager.get_status(session_id)
            init_payload = json.dumps({
                "type": "status",
                "session_id": session_id,
                "status": status_data["status"],
                "is_running": status_data["is_running"],
                "current_engine": status_data["current_engine"],
                "queue_position": status_data["queue_position"],
                "active_workers": status_data["active_workers"]
            })
            yield f"data: {init_payload}\n\n"

            while True:
                try:
                    data = q.get(timeout=3.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except queue.Empty:
                    yield f": ping {time.time()}\n\n"
        except GeneratorExit:
            queue_manager.unsubscribe(session_id, q)

    return Response(generate_events(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no"
    })

@app.route("/api/exports", methods=["GET"])
def list_exports():
    session_id, ws_dir = get_session_info()
    paths = get_workspace_paths(ws_dir)
    export_dir = paths["export"]

    expected_files = ["final_video.mp4", "pro_self_video.mp4", "insta_reel.mp4", "final_reel.mp4"]
    all_exports = []
    seen = set()

    for name in expected_files:
        full_path = os.path.join(export_dir, name)
        if os.path.exists(full_path):
            st = os.stat(full_path)
            seen.add(name)
            all_exports.append({
                "name": name,
                "size_mb": round(st.st_size / (1024 * 1024), 2),
                "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                "exists": True
            })
        else:
            all_exports.append({
                "name": name,
                "size_mb": 0,
                "mtime": "-",
                "exists": False
            })

    if os.path.exists(export_dir):
        for f in os.listdir(export_dir):
            if f.lower().endswith(".mp4") and f not in seen:
                full_path = os.path.join(export_dir, f)
                st = os.stat(full_path)
                all_exports.append({
                    "name": f,
                    "size_mb": round(st.st_size / (1024 * 1024), 2),
                    "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                    "exists": True
                })

    return jsonify({"exports": all_exports, "session_id": session_id})

@app.route("/api/video/<filename>", methods=["GET"])
def stream_video(filename):
    session_id, ws_dir = get_session_info()
    paths = get_workspace_paths(ws_dir)
    clean_name = os.path.basename(filename)
    file_path = os.path.join(paths["export"], clean_name)
    return send_file_partial(file_path)

@app.route("/api/download/<filename>", methods=["GET"])
def download_export(filename):
    session_id, ws_dir = get_session_info()
    paths = get_workspace_paths(ws_dir)
    clean_name = os.path.basename(filename)
    file_path = os.path.join(paths["export"], clean_name)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True, download_name=clean_name)
    return jsonify({"error": "File not found"}), 404

# --- Video Feedback & AI Studio Chat ---
FEEDBACK_LOG_PATH = os.path.join(BASE_DIR, "user_feedback.json")

def generate_assistant_reply(user_msg, active_vid=""):
    msg = user_msg.lower()
    vid_hint = f" regarding '{active_vid}'" if active_vid else ""
    if any(k in msg for k in ["caption", "sub", "subtitle", "word", "text", "timing"]):
        return (f"✍️ Caption Feedback Noted{vid_hint}! Timings are generated via Faster-Whisper int8 model. "
                f"Tip: If captions drift, make sure the voiceover has clear pronunciation and brief natural pauses.")
    elif any(k in msg for k in ["sound", "audio", "voice", "bgm", "music", "loud", "noise", "volume"]):
        return (f"🎙️ Audio Feedback Logged{vid_hint}! The studio automatically applies -25dB noise suppression, "
                f"radio EQ, and 6:1 BGM auto-ducking (-14 LUFS). If BGM feels too prominent, you can upload a milder BGM.")
    elif any(k in msg for k in ["quality", "blur", "4k", "1080p", "resolution", "pixel"]):
        return (f"📺 Resolution Guide: You can select '4K Ultra HD (2160x3840 @ 35 Mbps)' in Step 4 before rendering. "
                f"In 'Auto' mode, if your raw clip is 4K, 4K will be exported automatically!")
    elif any(k in msg for k in ["slow", "time", "speed", "stuck", "render", "fast"]):
        return (f"⚡ Render Engine Info: Rendering uses multi-threaded libx264 with CPU threading. "
                f"1-minute shorts typically take 15-45 seconds depending on effects and caption length.")
    elif any(k in msg for k in ["cut", "jump", "silence", "vlog"]):
        return (f"✂️ Jump-Cut Feedback: Vlog Pro automatically slices silences below -30dB with alternating "
                f"punch-zooms (1.08x) and DaVinci color grading.")
    else:
        return (f"✅ Thank you! Your feedback/issue report{vid_hint} has been successfully recorded. "
                f"Our studio automation pipeline monitors these logs to continuously improve video renders.")

@app.route("/api/chat/message", methods=["POST"])
def post_chat_message():
    session_id, ws_dir = get_session_info()
    data = request.get_json(silent=True) or {}
    user_text = (data.get("message") or "").strip()
    active_vid = (data.get("video") or "").strip()
    category = data.get("category", "feedback")

    if not user_text:
        return jsonify({"error": "Message is empty"}), 400

    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    bot_reply = generate_assistant_reply(user_text, active_vid)

    new_entry = {
        "id": f"msg_{int(time.time()*1000)}",
        "session_id": session_id,
        "video": active_vid,
        "category": category,
        "user_message": user_text,
        "assistant_reply": bot_reply,
        "timestamp": now_str
    }

    # Save to session feedback
    sess_fb_path = os.path.join(ws_dir, "feedback.json")
    sess_history = []
    if os.path.exists(sess_fb_path):
        try:
            with open(sess_fb_path, "r", encoding="utf-8") as f:
                sess_history = json.load(f)
        except Exception:
            sess_history = []
    sess_history.append(new_entry)
    with open(sess_fb_path, "w", encoding="utf-8") as f:
        json.dump(sess_history, f, ensure_ascii=False, indent=2)

    # Append to global feedback store
    try:
        global_history = []
        if os.path.exists(FEEDBACK_LOG_PATH):
            with open(FEEDBACK_LOG_PATH, "r", encoding="utf-8") as f:
                global_history = json.load(f)
        global_history.append(new_entry)
        with open(FEEDBACK_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(global_history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error logging global feedback: {e}")

    return jsonify({
        "status": "success",
        "entry": new_entry,
        "session_id": session_id
    })

@app.route("/api/chat/messages", methods=["GET"])
def get_chat_messages():
    session_id, ws_dir = get_session_info()
    sess_fb_path = os.path.join(ws_dir, "feedback.json")
    if os.path.exists(sess_fb_path):
        try:
            with open(sess_fb_path, "r", encoding="utf-8") as f:
                return jsonify({"messages": json.load(f), "session_id": session_id})
        except Exception:
            pass
    return jsonify({"messages": [], "session_id": session_id})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    host = "0.0.0.0"
    print(f"🎬 Video Studio Backend Multi-User Cloud SaaS ready on http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
