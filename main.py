from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
import subprocess
import requests
import tempfile
import os
from pathlib import Path

app = FastAPI()

class MergeRequest(BaseModel):
    video_url: HttpUrl
    audio_url: HttpUrl

def download_file(url: str, suffix: str) -> str:
    """Download file from URL to temporary location"""
    response = requests.get(url, stream=True)
    response.raise_for_status()

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    with open(temp_file.name, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    return temp_file.name

def merge_audio_video(video_path: str, audio_path: str, output_path: str):
    """Merge audio and video using ffmpeg"""
    command = [
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        output_path
    ]

    subprocess.run(command, check=True, capture_output=True)

@app.post("/merge")
async def merge(request: MergeRequest):
    """
    Endpoint to merge video and audio from URLs
    Expected JSON body:
    {
        "video_url": "https://...",
        "audio_url": "https://..."
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

        # Merge
        print("Merging audio and video...")
        merge_audio_video(video_path, audio_path, output_path)

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

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
