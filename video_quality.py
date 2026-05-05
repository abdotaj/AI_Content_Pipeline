import cv2
import numpy as np
import subprocess
import os

FFMPEG_PATH = "ffmpeg"

def enhance_video(input_path, output_path, vf):
    cmd = [
        FFMPEG_PATH,
        "-y", "-i", input_path,
        "-vf", vf,
        "-af", "loudnorm",
        "-c:v", "libx264", "-crf", "20",
        "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k",
        output_path
    ]

    result = subprocess.run(cmd)

    if result.returncode != 0:
        print("❌ FFmpeg FAILED:", output_path)
        return False

    if not os.path.exists(output_path):
        print("❌ Output missing:", output_path)
        return False

    print("✅ Created:", output_path)
    return True


def process_video(input_path):
    print("\n🚀 Processing:", input_path)

    # 🔥 Output folder
    output_dir = "output_videos"
    os.makedirs(output_dir, exist_ok=True)

    base_name = os.path.basename(input_path)
    name, _ = os.path.splitext(base_name)

    # =========================
    # 🟢 LONG (YouTube)
    # =========================
    vf_long = (
        "scale=1920:-2,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
        "eq=brightness=0.05:contrast=1.08:saturation=1.05"
    )

    output_long = os.path.join(output_dir, f"{name}_long.mp4")

    print("\n🎬 Creating LONG version...")
    long_ok = enhance_video(input_path, output_long, vf_long)

    # =========================
    # 🔵 SHORT (Vertical)
    # =========================
    vf_short = (
        "scale=-2:1920,"
        "crop=1080:1920,"
        "eq=brightness=0.12:contrast=1.1:saturation=1.08"
    )

    output_short = os.path.join(output_dir, f"{name}_short.mp4")

    print("\n📱 Creating SHORT version...")
    short_ok = enhance_video(input_path, output_short, vf_short)

    print("\n📂 DONE")
    print("LONG :", output_long)
    print("SHORT:", output_short)
    return long_ok, short_ok


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python script.py video.mp4")
        exit()

    video = sys.argv[1]

    if not os.path.exists(video):
        print("File not found")
        exit()

    process_video(video)