# clip-thumbnailer-service

Generate thumbnails from videos using ffmpeg + CLIP-based frame selection, with optional title overlay and upload to Bunny Storage or S3. FastAPI HTTP API included.

## Features
- **Frame extraction**: sample frames across the video via ffmpeg
- **CLIP selection**: pick best frame via CLIP text-image similarity
- **Exact sizing**: crop/scale to target WxH
- **Overlay**: optional title bar and text
- **Uploads**: Bunny Storage (recommended) or S3; or save local for dev
- **API**: FastAPI endpoints `/generate` and `/upload-generate`

## Prerequisites
- Python 3.9+
- ffmpeg and ffprobe in PATH (or set `FFMPEG_BIN` and `FFPROBE_BIN`)
- Git
- (Optional) GPU + matching PyTorch CUDA wheel

## Setup
```bash
python -m venv .venv
# Windows
.\.venv\Scripts\Activate.ps1
# macOS/Linux
source .venv/bin/activate

pip install --upgrade pip
# Choose CPU or CUDA (see pytorch.org for CUDA)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

copy .env.example .env  # or cp .env.example .env
```

### Configure backend
- Local only (easiest): set in `.env`
  - `UPLOAD_BACKEND=local`
- Bunny Storage:
  - `UPLOAD_BACKEND=bunny`
  - `BUNNY_STORAGE_ZONE=...`
  - `BUNNY_ACCESS_KEY=...`
  - `BUNNY_STORAGE_REGION_HOST=storage.bunnycdn.com`
  - `BUNNY_CDN_BASE_URL=https://your-pullzone.b-cdn.net`
- Optional ffmpeg explicit paths (if PATH not updated):
  - `FFMPEG_BIN=C:\\ffmpeg\\bin\\ffmpeg.exe`
  - `FFPROBE_BIN=C:\\ffmpeg\\bin\\ffprobe.exe`

## Run the server
```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Test the API
### Upload a file (multipart) – triggers CLIP if you omit `time`
```powershell
curl.exe -X POST "http://127.0.0.1:8000/upload-generate" `
  -F 'file=@"D:\\thumbnail maker\\example.mp4"' `
  -F "title=Local Test" `
  -F "key_prefix=thumbs/"
```
Response:
```json
{"url":"thumbnails/thumbs/<uuid>.jpg","s3_key":"thumbs/<uuid>.jpg","width":1280,"height":720}
```
Open locally via static route:
`http://127.0.0.1:8000/static/thumbnails/thumbs/<uuid>.jpg`

### JSON with local or HTTP path
```powershell
curl.exe -X POST "http://127.0.0.1:8000/generate" -H "Content-Type: application/json" `
  -d "{\"video_url\":\"D:\\\\thumbnail maker\\\\example.mp4\",\"width\":1280,\"height\":720}"
```

### Fixed frame (no CLIP)
Add `time` (seconds) in either endpoint to grab that exact frame.

## Notes
- First run downloads CLIP weights (~400MB).
- CPU-only works but is slower; prefer GPU for throughput.
- For production: queue jobs (Celery/RQ), add rate limiting & monitoring.