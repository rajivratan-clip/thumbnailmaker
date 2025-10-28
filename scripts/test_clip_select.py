# Quick script to test the CLIP selector standalone.
# Usage:
#   python scripts/test_clip_select.py /path/to/video.mp4
import sys
from pathlib import Path
from app.clip_selector import select_best_frame_by_clip

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_clip_select.py /path/to/video.mp4_or_http_url")
        return
    video = sys.argv[1]
    prompts = [
        "The most interesting and engaging frame from the video",
        "A frame showing the main subject clearly",
        "The frame with the most visual activity and highest image quality"
    ]
    best_path, best_ts = select_best_frame_by_clip(video_url=video, text_prompts=prompts, num_segments=12)
    print("Best frame saved at:", best_path)
    print("Timestamp selected:", best_ts)

if __name__ == '__main__':
    main()