import subprocess
import sys
from pathlib import Path

def merge_audio_video(video_path, audio_path, output_path):
    if not Path(video_path).exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    command = [
        "ffmpeg",
        "-y",                    # overwrite output
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",          # no re-encode video
        "-c:a", "aac",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        output_path
    ]

    subprocess.run(command, check=True)
    print(f"✅ Output saved to {output_path}")

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python main.py <video.mp4> <audio.mp3> <output.mp4>")
        sys.exit(1)

    merge_audio_video(
        sys.argv[1],
        sys.argv[2],
        sys.argv[3]
    )
