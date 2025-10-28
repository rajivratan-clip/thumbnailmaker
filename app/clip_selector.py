import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, List, Tuple
from PIL import Image

import torch
from transformers import CLIPProcessor, CLIPModel


def _ensure_ffmpeg_available():
    try:
        ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg")
        ffprobe_bin = os.getenv("FFPROBE_BIN", "ffprobe")
        subprocess.run([ffmpeg_bin, "-version"], check=True, capture_output=True)
        subprocess.run([ffprobe_bin, "-version"], check=True, capture_output=True)
    except Exception as e:
        raise RuntimeError("ffmpeg/ffprobe not found in PATH") from e


def _ffprobe_duration(video_url: str) -> float:
    """Return duration in seconds using ffprobe; raises on failure."""
    _ensure_ffmpeg_available()
    ffprobe_bin = os.getenv("FFPROBE_BIN", "ffprobe")
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def _extract_frame_at_time(video_url: str, t: float, out_path: Path) -> None:
    _ensure_ffmpeg_available()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-ss",
        str(max(0.0, t)),
        "-i",
        video_url,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


def _linspace(start: float, end: float, num: int) -> List[float]:
    if num <= 1:
        return [start]
    step = (end - start) / float(num - 1)
    return [start + i * step for i in range(num)]


def select_best_frame_by_clip(
    video_url: str,
    text_prompts: Iterable[str],
    num_segments: int = 12,
    model_name: str = "openai/clip-vit-base-patch32",
) -> Tuple[Path, float]:
    """
    Extract frames across the video and select the best frame using CLIP similarity.
    Returns (best_frame_path, timestamp_seconds).
    """
    duration = _ffprobe_duration(video_url)
    if not (duration and duration > 0):
        raise RuntimeError("Could not determine video duration")

    # Sample timestamps, avoid first/last small margins
    margin = max(0.5, duration * 0.02)
    times = _linspace(margin, max(margin, duration - margin), num_segments)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = CLIPModel.from_pretrained(model_name).to(device)
    processor = CLIPProcessor.from_pretrained(model_name)

    with tempfile.TemporaryDirectory() as tmpd:
        frame_paths: List[Path] = []
        for idx, t in enumerate(times):
            p = Path(tmpd) / f"frame_{idx:03d}.jpg"
            try:
                _extract_frame_at_time(video_url, t, p)
                frame_paths.append(p)
            except Exception:
                continue

        if not frame_paths:
            raise RuntimeError("Failed to extract frames from video")

        images = [Image.open(p).convert("RGB") for p in frame_paths]
        texts = list(text_prompts)
        if not texts:
            texts = [
                "The most interesting and engaging frame from the video",
                "A frame showing the main subject clearly",
            ]

        inputs = processor(text=texts, images=images, return_tensors="pt", padding=True)
        for k in inputs:
            inputs[k] = inputs[k].to(device)

        with torch.no_grad():
            outputs = model(**inputs)
            image_embeds = outputs.image_embeds  # [B_images, D]
            text_embeds = outputs.text_embeds    # [B_text, D]

        # Normalize
        image_embeds = image_embeds / image_embeds.norm(p=2, dim=-1, keepdim=True)
        text_embeds = text_embeds / text_embeds.norm(p=2, dim=-1, keepdim=True)

        # Similarity: [B_text, D] x [D, B_images] -> [B_text, B_images]
        sims = text_embeds @ image_embeds.T
        # Average across prompts -> [B_images]
        avg_sims = sims.mean(dim=0)
        best_idx = int(torch.argmax(avg_sims).item())
        best_time = times[best_idx]
        best_path = frame_paths[best_idx]
        # Move the best frame out of tmp to persist for caller
        final_path = Path(tempfile.mkdtemp()) / "best.jpg"
        Image.open(best_path).save(final_path, quality=95)
        return final_path, best_time