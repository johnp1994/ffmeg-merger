from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
from typing import List, Optional
import subprocess
import requests
import tempfile
import os
from pathlib import Path

app = FastAPI()

class MergeRequest(BaseModel):
    video_url: HttpUrl
    audio_url: HttpUrl
    target_duration: Optional[float] = None

class StitchRequest(BaseModel):
    video_urls: List[HttpUrl]

def download_file(url: str, suffix: str) -> str:
    """Download file from URL to temporary location"""
    response = requests.get(url, stream=True)
    response.raise_for_status()

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    with open(temp_file.name, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    return temp_file.name

def get_duration(file_path: str) -> float:
    """Get duration of media file using ffprobe"""
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    return float(subprocess.check_output(probe_cmd).decode().strip())

def merge_audio_video(video_path: str, audio_path: str, output_path: str, target_duration: Optional[float] = None):
    """Merge audio and video with exact duration matching"""
    
    # Get actual durations
    video_duration = get_duration(video_path)
    audio_duration = get_duration(audio_path)
    
    print(f"Video duration: {video_duration}s, Audio duration: {audio_duration}s")
    
    # Determine sync duration
    if target_duration:
        sync_duration = target_duration
        print(f"Using target duration: {sync_duration}s")
    else:
        sync_duration = min(video_duration, audio_duration)
        print(f"Using minimum duration: {sync_duration}s")
    
    # Calculate speed adjustment for video to match audio
    speed_factor = video_duration / audio_duration if audio_duration > 0 else 1.0
    
    command = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        # Trim both to exact duration and adjust video speed to match audio
        "-filter_complex", 
        f"[0:v]setpts=PTS*{speed_factor},trim=duration={sync_duration}[v];[1:a]atrim=0:{sync_duration}[a]",
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_path
    ]
    
    subprocess.run(command, check=True, capture_output=True)

def stitch_videos(video_paths: List[str], output_path: str):
    """Concatenate multiple videos using ffmpeg"""
    # Create a temporary file list for ffmpeg concat
    concat_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt')

    for video_path in video_paths:
        # Write each video path to the concat file
        concat_file.write(f"file '{video_path}'\n")

    concat_file.close()

    command = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file.name,
        "-c", "copy",
        output_path
    ]

    subprocess.run(command, check=True, capture_output=True)

    # Clean up concat file
    os.unlink(concat_file.name)

@app.post("/merge")
async def merge(request: MergeRequest):
    """
    Endpoint to merge video and audio from URLs
    Expected JSON body:
    {
        "video_url": "https://...",
        "audio_url": "https://...",
        "target_duration": 8.0  (optional)
    }
    """
    try:
        video_url = str(request.video_url)
        audio_url = str(request.audio_url)

        # Download files
        print(f"Downloading video from: {video_url}")
        video_path = download_file(video_url, '.mp4')

        print(f"Downloading audio from: {audio_url}")
        audio_path = download_file(audio_url, '.mp3')

        # Create output file
        output_path = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4').name

        # Merge with target duration if provided
        print("Merging audio and video...")
        merge_audio_video(video_path, audio_path, output_path, request.target_duration)

        # Clean up input files
        os.unlink(video_path)
        os.unlink(audio_path)

        # Return the merged file
        return FileResponse(
            output_path,
            media_type='video/mp4',
            filename='merged_output.mp4'
        )

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Failed to download file: {str(e)}")
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"FFmpeg error: {e.stderr.decode()}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/stitch")
async def stitch(request: StitchRequest):
    """
    Endpoint to stitch multiple videos together
    Expected JSON body:
    {
        "video_urls": [
            "https://...",
            "https://...",
            "https://..."
        ]
    }
    """
    try:
        if len(request.video_urls) < 2:
            raise HTTPException(status_code=400, detail="At least 2 videos are required")

        video_paths = []

        # Download all videos
        for i, video_url in enumerate(request.video_urls):
            print(f"Downloading video {i+1}/{len(request.video_urls)} from: {video_url}")
            video_path = download_file(str(video_url), '.mp4')
            video_paths.append(video_path)

        # Create output file
        output_path = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4').name

        # Stitch videos
        print("Stitching videos together...")
        stitch_videos(video_paths, output_path)

        # Clean up input files
        for video_path in video_paths:
            os.unlink(video_path)

        # Return the stitched file
        return FileResponse(
            output_path,
            media_type='video/mp4',
            filename='stitched_output.mp4'
        )

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Failed to download file: {str(e)}")
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"FFmpeg error: {e.stderr.decode()}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)