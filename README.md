# Video Processing API

A FastAPI-based service for merging audio with video and stitching multiple videos together using FFmpeg.

## Features

- **Merge Audio and Video**: Combine separate video and audio files with perfect synchronization
- **Stitch Videos**: Concatenate multiple videos into a single output
- **Duration Control**: Ensure exact timing alignment with optional target duration
- **Automatic Sync**: Adjusts video playback speed to match audio duration

## Prerequisites

- Python 3.7+
- FFmpeg installed and accessible in system PATH
- ffprobe (usually comes with FFmpeg)

## Installation

1. Install dependencies:
```bash