# 1. Use Debian-based Python image
FROM python:3.12-slim

# 2. Install system dependencies, ffmpeg, yt-dlp (with [default] extra for EJS/JS-challenge scripts)
RUN apt-get update && apt-get install -y \
    ffmpeg curl unzip && \
    pip install --no-cache-dir --upgrade --break-system-packages "yt-dlp[default]" && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 3. Install deno (glibc compatible, required JS runtime for YouTube n-challenge solving)
RUN curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip \
    -o /tmp/deno.zip && \
    unzip /tmp/deno.zip -d /usr/local/bin && \
    chmod +x /usr/local/bin/deno && \
    rm /tmp/deno.zip

# 3b. Sanity check at build time: fail the build early if yt-dlp/deno aren't wired up correctly
RUN yt-dlp --version && deno --version && \
    yt-dlp --list-extractors > /dev/null

# 4. Create application directory
WORKDIR /usr/src/app

# 5. Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copy application source code
COPY . .

# 7. Expose application port
EXPOSE 9999

# 8. Start application
CMD ["python", "main.py"]
