# Hugging Face Spaces & Cloud-Ready Multi-User Video Studio Dockerfile
FROM python:3.11-slim

# Install system dependencies: FFmpeg, DejaVu fonts, OpenCV & Media libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    libgl1 \
    libglib2.0-0 \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces runs as user 1000
RUN useradd -m -u 1000 user

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PORT=7860

WORKDIR $HOME/app

# Install Python requirements
COPY --chown=user requirements.txt $HOME/app/
RUN pip install --no-cache-dir --user -r requirements.txt

# Copy all application files with proper permissions
COPY --chown=user . $HOME/app

EXPOSE 7860

# Start server
CMD ["python", "server.py"]
