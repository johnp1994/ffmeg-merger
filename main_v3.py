from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
from typing import List, Optional, Dict
import subprocess
import requests
import tempfile
import os
import zipfile
import base64
import uuid
import time
from io import BytesIO
from pathlib import Path

app = FastAPI()

# In-memory storage for temporary images
# Format: {image_id: {"path": str, "expires_at": float, "mime_type": str}}
temp_images: Dict[str, Dict] = {}

def cleanup_expired_images():
    """Remove expired temporary images"""
    current_time = time.time()
    expired_ids = [
        image_id for image_id, data in temp_images.items()
        if data["expires_at"] < current_time
    ]
    
    for image_id in expired_ids:
        try:
            os.unlink(temp_images[image_id]["path"])
        except Exception:
            pass
        del temp_images[image_id]
    
    return len(expired_ids)

class MergeRequest(BaseModel):
    video_url: HttpUrl
    audio_url: HttpUrl
    target_duration: Optional[float] = None

class StitchRequest(BaseModel):
    video_urls: List[HttpUrl]

class FrameExtractRequest(BaseModel):
    video_url: HttpUrl
    timestamps: List[float]  # List of timestamps in seconds
    return_urls: Optional[bool] = False  # If True, return temporary URLs instead of base64
    url_expiry_seconds: Optional[int] = 300  # URL expiry time in seconds (default 5 minutes)

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

def extract_frames(video_path: str, timestamps: List[float]) -> List[str]:
    """Extract frames from video at specified timestamps"""
    frame_paths = []
    
    for i, timestamp in enumerate(timestamps):
        # Create temp file for this frame
        frame_path = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg').name
        
        command = [
            "ffmpeg",
            "-y",
            "-ss", str(timestamp),  # Seek to timestamp
            "-i", video_path,
            "-frames:v", "1",  # Extract only 1 frame
            "-q:v", "2",  # High quality (1-31, lower is better)
            frame_path
        ]
        
        subprocess.run(command, check=True, capture_output=True)
        frame_paths.append(frame_path)
    
    return frame_paths

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

@app.post("/extract-frames")
async def extract_frames_endpoint(request: FrameExtractRequest):
    """
    Endpoint to extract frames from a video at specified timestamps
    Expected JSON body:
    {
        "video_url": "https://...",
        "timestamps": [1.5, 3.0, 5.5],
        "return_urls": false,  // Optional: if true, returns temporary URLs instead of base64
        "url_expiry_seconds": 300  // Optional: URL expiry time (default 300s/5min)
    }
    Returns JSON with base64-encoded images or temporary URLs for n8n integration
    """
    try:
        if not request.timestamps:
            raise HTTPException(status_code=400, detail="At least one timestamp is required")
        
        video_url = str(request.video_url)
        
        # Download video
        print(f"Downloading video from: {video_url}")
        video_path = download_file(video_url, '.mp4')
        
        # Get video duration to validate timestamps
        video_duration = get_duration(video_path)
        
        # Validate timestamps
        for ts in request.timestamps:
            if ts < 0 or ts > video_duration:
                os.unlink(video_path)
                raise HTTPException(
                    status_code=400, 
                    detail=f"Timestamp {ts}s is out of range. Video duration is {video_duration}s"
                )
        
        # Extract frames
        print(f"Extracting {len(request.timestamps)} frames...")
        frame_paths = extract_frames(video_path, request.timestamps)
        
        # Clean up video file
        os.unlink(video_path)
        
        # Cleanup expired images before adding new ones
        cleanup_expired_images()
        
        frames_data = []
        
        if request.return_urls:
            # Return temporary URLs
            expiry_time = time.time() + request.url_expiry_seconds
            
            for i, frame_path in enumerate(frame_paths):
                image_id = str(uuid.uuid4())
                
                # Store frame info
                temp_images[image_id] = {
                    "path": frame_path,
                    "expires_at": expiry_time,
                    "mime_type": "image/jpeg"
                }
                
                # Get base URL from request (you'll need to set this to your actual Cloud Run URL)
                temp_url = f"/temp-image/{image_id}"
                
                frames_data.append({
                    "timestamp": request.timestamps[i],
                    "frame_number": i,
                    "image_url": temp_url,
                    "expires_at": expiry_time,
                    "expires_in_seconds": request.url_expiry_seconds,
                    "mime_type": "image/jpeg",
                    "filename": f"frame_{i}_at_{request.timestamps[i]}s.jpg"
                })
        else:
            # Return base64 encoded images
            for i, frame_path in enumerate(frame_paths):
                with open(frame_path, 'rb') as f:
                    image_data = f.read()
                    base64_image = base64.b64encode(image_data).decode('utf-8')
                    
                    frames_data.append({
                        "timestamp": request.timestamps[i],
                        "frame_number": i,
                        "image_base64": base64_image,
                        "mime_type": "image/jpeg",
                        "filename": f"frame_{i}_at_{request.timestamps[i]}s.jpg"
                    })
                
                # Clean up frame file
                os.unlink(frame_path)
        
        return {
            "success": True,
            "video_url": video_url,
            "video_duration": video_duration,
            "frames_count": len(frames_data),
            "return_type": "urls" if request.return_urls else "base64",
            "frames": frames_data
        }
        
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Failed to download file: {str(e)}")
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"FFmpeg error: {e.stderr.decode()}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/temp-image/{image_id}")
async def get_temp_image(image_id: str):
    """
    Serve a temporary image by its ID
    Images expire after the specified time and are automatically cleaned up
    """
    # Cleanup expired images first
    cleanup_expired_images()
    
    if image_id not in temp_images:
        raise HTTPException(status_code=404, detail="Image not found or expired")
    
    image_data = temp_images[image_id]
    
    # Check if expired
    if image_data["expires_at"] < time.time():
        try:
            os.unlink(image_data["path"])
        except Exception:
            pass
        del temp_images[image_id]
        raise HTTPException(status_code=410, detail="Image has expired")
    
    # Return the image
    return FileResponse(
        image_data["path"],
        media_type=image_data["mime_type"]
    )

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)