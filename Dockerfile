# 1. Use Debian-based Python image
FROM python:3.12-slim

# 2. Install system dependencies and ffmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg curl unzip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 3. Install deno (glibc compatible, required JS runtime for YouTube n-challenge solving)
RUN curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip \
    -o /tmp/deno.zip && \
    unzip /tmp/deno.zip -d /usr/local/bin && \
    chmod +x /usr/local/bin/deno && \
    rm /tmp/deno.zip

# 4. Create application directory
WORKDIR /usr/src/app

# 5. Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copy application source code
COPY . .

# 7. Install/upgrade yt-dlp LAST on purpose: it is the piece YouTube breaks most
#    often, and installing it in an earlier layer lets Docker reuse a cached — and
#    therefore stale — version, which surfaces as "Failed to extract any player
#    response". Re-running it on every build keeps the extractor current.
#    Also fails the build early if yt-dlp/deno are not wired up correctly.
RUN pip install --no-cache-dir --upgrade "yt-dlp[default]" && \
    yt-dlp --version && deno --version && \
    yt-dlp --list-extractors > /dev/null

# 8. Expose application port
EXPOSE 9999

# 9. Start application
CMD ["python", "main.py"]
