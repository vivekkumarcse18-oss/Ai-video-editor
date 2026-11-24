# Cloud-Ready Multi-User Video Studio Dockerfile
FROM python:3.11-slim

# Install system dependencies including FFmpeg, DejaVu fonts, and media libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Set environment defaults
ENV PORT=5050
ENV PYTHONUNBUFFERED=1

EXPOSE 5050

# Run the multi-user server binding to 0.0.0.0:$PORT
CMD ["python", "server.py"]
