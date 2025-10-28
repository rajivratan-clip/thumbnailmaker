import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import boto3
import os
import shutil
import requests

# import clip selector
from .clip_selector import select_best_frame_by_clip

# Configuration via env vars (S3 or Bunny Storage)
# S3 (optional/back-compat)
S3_BUCKET = os.getenv("THUMB_S3_BUCKET")
S3_REGION = os.getenv("THUMB_S3_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

# Bunny Storage
BUNNY_STORAGE_ZONE = os.getenv("BUNNY_STORAGE_ZONE")
BUNNY_ACCESS_KEY = os.getenv("BUNNY_ACCESS_KEY")  # Storage API key
# Region host like "storage.bunnycdn.com" or "ny.storage.bunnycdn.com"
BUNNY_STORAGE_REGION_HOST = os.getenv("BUNNY_STORAGE_REGION_HOST", "storage.bunnycdn.com")
# Public CDN base, e.g. https://your-pullzone.b-cdn.net
BUNNY_CDN_BASE_URL = os.getenv("BUNNY_CDN_BASE_URL")

# Explicit backend override ("bunny", "s3", or "local")
UPLOAD_BACKEND = os.getenv("UPLOAD_BACKEND")

def _ensure_ffmpeg_available():
    try:
        ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg")
        subprocess.run([ffmpeg_bin, "-version"], check=True, capture_output=True)
    except Exception as e:
        raise RuntimeError("ffmpeg not found in PATH") from e

def _extract_frame(video_url: str, out_path: Path, width: int, height: int, time: float = None):
    """
    Lower-level extraction for exact requested timestamps.
    """
    _ensure_ffmpeg_available()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    vf_scale_crop = (
        f"scale=trunc(iw*max({width}/iw\\,{height}/ih)):trunc(ih*max({width}/iw\\,{height}/ih)),"
        f"crop={width}:{height}"
    )
    if time is not None:
        ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg")
        cmd = [
            ffmpeg_bin, "-y",
            "-ss", str(time),
            "-i", video_url,
            "-frames:v", "1",
            "-vf", vf_scale_crop,
            "-q:v", "3",
            str(out_path)
        ]
        subprocess.run(cmd, check=True)
    else:
        # fallback: grab frame at 1s
        ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg")
        cmd2 = [
            ffmpeg_bin, "-y",
            "-ss", "1",
            "-i", video_url,
            "-frames:v", "1",
            "-vf", vf_scale_crop,
            "-q:v", "3",
            str(out_path)
        ]
        subprocess.run(cmd2, check=True)

def _draw_title(image_path: Path, title: str, font_path: str = None, font_size: int = None):
    if not title:
        return
    im = Image.open(image_path).convert("RGBA")
    w, h = im.size
    if not font_size:
        font_size = max(34, w // 24)
    try:
        font = ImageFont.truetype(font_path or "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(im, "RGBA")

    # simple wrapping
    padding_h = int(w * 0.04)
    padding_v = int(h * 0.02)
    max_width = int(w - 2 * padding_h)
    words = title.split()
    lines = []
    cur = ""
    for word in words:
        test = (cur + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        tw, th = (bbox[2] - bbox[0], bbox[3] - bbox[1])
        if tw <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)

    # compute text block height for a tight bottom band
    line_spacing = int(font_size * 0.15)
    text_heights = []
    text_widths = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_widths.append(bbox[2] - bbox[0])
        text_heights.append(bbox[3] - bbox[1])
    block_text_height = sum(text_heights) + line_spacing * max(0, len(lines) - 1)
    rect_top = max(0, h - padding_v - block_text_height - padding_v)

    # draw a minimal-height semi-transparent bar just behind text
    draw.rectangle([(0, rect_top), (w, h)], fill=(0,0,0,180))

    # render lines centered
    y = rect_top + padding_v
    for idx, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        tw, th = (bbox[2] - bbox[0], bbox[3] - bbox[1])
        x = (w - tw) // 2
        stroke = max(1, font_size // 14)
        for dx in range(-stroke, stroke+1):
            for dy in range(-stroke, stroke+1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x+dx, y+dy), line, font=font, fill=(0,0,0,255))
        draw.text((x, y), line, font=font, fill=(255,255,255,255))
        y += th + line_spacing

    im = im.convert("RGB")
    im.save(image_path, quality=90)

def _upload_to_s3(local_path: Path, s3_key: str) -> str:
    if not S3_BUCKET:
        raise RuntimeError("S3 bucket not configured (THUMB_S3_BUCKET)")
    session = boto3.session.Session(
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=S3_REGION
    )
    s3 = session.client("s3")
    s3.upload_file(str(local_path), S3_BUCKET, s3_key, ExtraArgs={"ContentType": "image/jpeg", "ACL": "public-read"})
    # If you front S3 with a CDN, set BUNNY_CDN_BASE_URL or any CDN base
    if BUNNY_CDN_BASE_URL:
        return f"{BUNNY_CDN_BASE_URL.rstrip('/')}/{s3_key.lstrip('/')}"
    return f"https://{S3_BUCKET}.s3.amazonaws.com/{s3_key}"

def _upload_to_bunny(local_path: Path, storage_key: str) -> str:
    """
    Upload to Bunny Storage using HTTP PUT API.
    storage_key is the path inside the storage zone, e.g. "thumbnails/uuid.jpg".
    """
    if not (BUNNY_STORAGE_ZONE and BUNNY_ACCESS_KEY):
        raise RuntimeError("Bunny Storage not configured (BUNNY_STORAGE_ZONE, BUNNY_ACCESS_KEY)")

    url = f"https://{BUNNY_STORAGE_REGION_HOST.rstrip('/')}/{BUNNY_STORAGE_ZONE}/{storage_key.lstrip('/')}"
    headers = {
        "AccessKey": BUNNY_ACCESS_KEY,
        "Content-Type": "image/jpeg"
    }
    with open(local_path, "rb") as f:
        resp = requests.put(url, headers=headers, data=f)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Bunny upload failed: {resp.status_code} {resp.text}")

    # Build public URL via CDN base if provided, else direct storage (not recommended for prod)
    if BUNNY_CDN_BASE_URL:
        return f"{BUNNY_CDN_BASE_URL.rstrip('/')}/{storage_key.lstrip('/')}"
    # Fallback direct storage URL (private/unoptimized):
    return f"https://{BUNNY_STORAGE_REGION_HOST.rstrip('/')}/{BUNNY_STORAGE_ZONE}/{storage_key.lstrip('/')}"

def _save_local(local_path: Path, rel_key: str) -> str:
    dest = Path("thumbnails") / rel_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(local_path, dest)
    # Expose via local static path (user can serve this via any static server)
    return str(dest.as_posix())

def generate_thumbnail_and_upload(video_url: str, out_key: str, width: int = 1280, height: int = 720, time: float = None, title: str = None) -> str:
    """
    - If time provided: extract that frame, resize/crop, overlay title, upload.
    - If time is None: run CLIP-based selector to pick best frame, then crop/resize (cover) to target, overlay, upload.
    """
    with tempfile.TemporaryDirectory() as tmpd:
        out_img = Path(tmpd) / "thumb.jpg"

        if time is None:
            # use CLIP selector to pick best candidate (returns path and timestamp)
            prompts = [
                "The most interesting and engaging frame from the video",
                "A frame showing the main subject clearly",
                "The frame with the most visual activity and highest image quality"
            ]
            try:
                best_path, best_ts = select_best_frame_by_clip(video_url=video_url, text_prompts=prompts, num_segments=12)
                # crop/resize the selected frame to exact width x height using ffmpeg (local file input)
                _ensure_ffmpeg_available()
                vf_scale_crop = (
                    f"scale=trunc(iw*max({width}/iw\\,{height}/ih)):trunc(ih*max({width}/iw\\,{height}/ih)),"
                    f"crop={width}:{height}"
                )
                ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg")
                cmd = [
                    ffmpeg_bin, "-y",
                    "-i", str(best_path),
                    "-frames:v", "1",
                    "-vf", vf_scale_crop,
                    "-q:v", "3",
                    str(out_img)
                ]
                subprocess.run(cmd, check=True)
            except Exception as e:
                # fallback: extract a frame at 1s
                _extract_frame(video_url, out_img, width, height, time=1.0)
        else:
            _extract_frame(video_url, out_img, width, height, time=time)

        # overlay title if provided
        if title:
            _draw_title(out_img, title)

        # upload based on configured backend
        backend = (UPLOAD_BACKEND or "").lower()
        if not backend:
            if BUNNY_STORAGE_ZONE and BUNNY_ACCESS_KEY:
                backend = "bunny"
            elif S3_BUCKET:
                backend = "s3"
            else:
                backend = "local"

        if backend == "bunny":
            return _upload_to_bunny(out_img, out_key)
        if backend == "s3":
            return _upload_to_s3(out_img, out_key)
        if backend == "local":
            return _save_local(out_img, out_key)
        raise RuntimeError(f"Unknown UPLOAD_BACKEND: {backend}")