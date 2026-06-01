FROM python:3.13-slim

# Real ffmpeg (Debian build with working drawtext) + system fonts + fontconfig.
# This replaces the broken static-ffmpeg drawtext on Railway.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fontconfig \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway sets PORT; app reads it.
CMD ["python", "app.py"]
