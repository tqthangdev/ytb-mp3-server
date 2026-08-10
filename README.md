# ytb-mp3-server

Server chuyển đổi video YouTube sang MP3, viết bằng **Python (FastAPI + python-socketio)**. Nhận yêu cầu từ client (app React Native / web), tải audio qua `yt-dlp`, convert sang MP3 bằng `ffmpeg`, báo tiến độ realtime qua Socket.IO, rồi phục vụ file cho client tải về.

> Phiên bản trước đây viết bằng Node.js (Express + socket.io); đã chuyển toàn bộ sang Python. API contract giữ nguyên nên client cũ vẫn hoạt động.

## Kiến trúc

```
ytb-mp3-server/
├── main.py                  # Entry point: FastAPI app + Socket.IO server + queue init + graceful shutdown
├── routes/
│   └── download.py          # GET /download (queue job), GET /file/:jobId (tải file mp3)
├── services/
│   ├── job_queue.py         # Hàng đợi job (tối đa N job chạy cùng lúc, giới hạn tổng job)
│   ├── ytdlp.py             # Gọi yt-dlp: lấy metadata + download audio (subprocess)
│   ├── ffmpeg.py            # Convert m4a → mp3 (subprocess)
│   └── file_service.py      # Tiện ích file: sanitize tên, temp file, xoá, kích thước
├── utils/
│   └── url_util.py          # Chuẩn hoá URL YouTube (youtu.be/... → watch?v=...)
├── requirements.txt
├── Dockerfile
└── cookies.txt              # (tuỳ chọn) cookie YouTube — KHÔNG push lên git
```

## Yêu cầu

- Python 3.11+
- `yt-dlp` binary (`yt-dlp.exe` trên Windows đặt cạnh thư mục dự án, hoặc `yt-dlp` trong PATH trên Linux)
- `ffmpeg` trong PATH
- (Tuỳ chọn) `deno` — chỉ cần khi deploy Docker, dùng cho YouTube n-challenge solving

## Cài đặt & chạy

```bash
# 1. Tạo virtualenv và cài dependencies
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt

# 2. Chạy server (mặc định port 9999, đổi qua env PORT nếu cần)
python main.py
```

Kiểm tra server sống:

```bash
curl http://localhost:9999/health
# → {"status":"ok","queue":{"running":0,"waiting":0,"max_concurrent":1,"max_queue_length":20},"timestamp":"..."}
```

## API

### `GET /health`

Trạng thái server + thông tin queue.

### `GET /download?socketId=<socket_id>&url=<youtube_url>`

Ghi danh một job vào hàng đợi. **Trả về ngay lập tức** (không chờ tải xong):

```json
{ "jobId": "abc123_1786358433701_bb5469", "queued": false, "position": 0 }
```

- `socketId` (tuỳ chọn): id socket của client — server emit event `progress_update` về đúng client này. Bỏ qua nếu client không dùng socket.
- `queued: true` + `position` khi hàng đợi đang đầy (job xếp hàng chờ).
- Lỗi: thiếu `url` → `400`; queue đầy (quá 20 job) → `503`.

### `GET /file/:jobId`

Tải file MP3 đã convert xong. Trả `Content-Disposition: attachment; filename*=UTF-8''<title>.mp3`.

- Chưa có / đã tải / hết hạn → `404` hoặc `410`.
- File chỉ tồn tại trong **20 phút** kể từ khi ready; hết hạn sẽ bị xoá.
- File bị xoá ngay sau khi client tải xong (hoặc ngắt giữa chừng) — mỗi job chỉ tải được 1 lần.

## Socket.IO events

Client connect tới `http://<host>:9999/socket.io` (giao thức Socket.IO v4).

Server emit event **`progress_update`** về room của socket đã đăng ký trong `/download`:

```json
{
  "status": "queued | starting | fetching_info | downloading_server | converting | ready | error",
  "percent": 0,
  "url": "https://www.youtube.com/watch?v=...",
  "jobId": "abc123_1786358433701_bb5469",
  "position": 2,          // chỉ khi status = queued
  "fileUrl": "/file/abc123_...",  // chỉ khi status = ready
  "title": "Video title",          // chỉ khi status = ready
  "errorMessage": "..."            // chỉ khi status = error
}
```

Quy trình hoàn chỉnh: `starting` → `fetching_info` (5%) → `downloading_server` (20–70%) → `converting` (75%) → `ready` (95%) → client tải `/file/:jobId` → done. Nếu lỗi: `error` (0%).

## Hàng đợi (Job queue)

- **maxConcurrent = 1**: chỉ 1 job chạy cùng lúc (phù hợp plan miễn phí Render), các job sau xếp hàng.
- **maxQueueLength = 20**: giới hạn tổng job (running + waiting) để chống spam; vượt quá → `503`.
- Các hằng số này chỉnh ở đầu `main.py`.

## Graceful shutdown

Khi nhận `SIGTERM`: huỷ các job đang chờ, chờ tối đa 30s cho job đang chạy hoàn tất, rồi đóng server (force-exit sau 35s nếu kẹt).

## Docker

```bash
docker build -t ytb-mp3-server .
docker run -p 9999:9999 ytb-mp3-server
```

Image dựng sẵn `ffmpeg`, `yt-dlp[default]`, `deno` (xử lý YouTube n-challenge). Đổi port qua `-e PORT=xxxx` nếu cần.

## Lưu ý

- **`cookies.txt`**: nếu YouTube chặn, đặt file Netscape-format cookies tại thư mục dự án; server tự nhận diện và dùng (`--cookies`). File này **đã gitignore**, không bao giờ push lên repo.
- Temp file (`m4a`/`mp3`) nằm trong thư mục temp của hệ điều hành (`/tmp` trên Linux) và được dọn tự động: file ready quá 20 phút, file tải xong, file lỗi.
- Server chạy đơn luồng asyncio: mọi tác vụ (subprocess, socket, queue) đều async, không cần lock.
