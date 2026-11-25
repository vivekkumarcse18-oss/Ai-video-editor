---
title: AI Video Studio
emoji: 🎬
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# 🎬 AI Video Studio (Multi-User Cloud SaaS)

A modern, dark-mode, multi-user video editing studio and automation suite powered by Python, FFmpeg, MoviePy, OpenCV, and faster-whisper.

### 🌟 Features
- **Folder-First Workspace**: Clean 3-folder layout (`Raw video`, `References`, `audio`) + `Step 4: Export & Create Studio`.
- **Multi-User Session Isolation**: Dynamic `workspaces/<session_id>/` subfolders per browser user to prevent file collisions.
- **Concurrent Render Queue**: Thread pool worker queue (up to 3 simultaneous renders) with live per-session SSE terminal logs.
- **Auto 4K & 1080p Sense**: Smart resolution detection and proportional overlays.
- **Studio Audio Mastering**: Noise removal, Radio EQ, auto-ducking, and broadcast -14 LUFS mastering.
- **DaVinci Resolve & Premiere FX**: Warm highlight grading, whip-pan transitions, punch-zooms, and word-synced subtitles.
