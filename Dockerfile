# Auxx In web app: Flask + the downloaders (yt-dlp, ffmpeg, ytmusicapi).
# Deno is the JavaScript runtime yt-dlp needs for YouTube.
FROM denoland/deno:bin-2.9.8 AS deno

FROM python:3.13-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=deno /deno /usr/local/bin/deno
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY web/ /app/
WORKDIR /app
ENV HOME=/data DATA_ROOT=/data MUSIC_ROOT=/music PYTHONUNBUFFERED=1
EXPOSE 8140
CMD ["python3", "/app/app.py"]
