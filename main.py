import os
import re
import sys
import threading
import time
import urllib.parse
import asyncio
from typing import Optional

# Web Framework & Server
from fastapi import FastAPI, UploadFile, File, Query, HTTPException
from fastapi.responses import JSONResponse
import uvicorn
import gradio as gr

# Media Tools
import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget
from shazamio import Shazam

# Dummy spaces module fallback (prevents errors outside HF Spaces)
try:
    import spaces
except ImportError:
    class DummySpaces:
        def GPU(self, func):
            return func
    spaces = DummySpaces()

# Initialize FastAPI App
app = FastAPI(title="Optimized Audio Recognition Engine")

# ==========================================
# PLATFORM STUBS & HELPER FUNCTIONS
# ==========================================

@spaces.GPU
def dummy_gpu_trigger():
    """Satisfies platform status triggers if deployed alongside Gradio."""
    return "Core Status: Active"


async def recognize_audio_bytes(file_bytes: bytes) -> dict:
    """Invokes shazamio engine using raw audio bytes."""
    shazam = Shazam()
    return await shazam.recognize(file_bytes)


async def normalize_audio_with_ffmpeg(input_bytes: bytes) -> bytes:
    """Non-blocking async FFmpeg pipe conversion to 16-bit PCM WAV."""
    try:
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg', '-i', 'pipe:0', '-f', 'wav', '-acodec', 'pcm_s16le', '-ar', '44100', 'pipe:1',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate(input=input_bytes)
        
        if proc.returncode != 0:
            print(f"FFmpeg conversion warning: {stderr.decode('utf-8', errors='ignore')}")
            return input_bytes
        return stdout
    except Exception as err:
        print(f"FFmpeg pipeline bypass (using raw bytes): {err}")
        return input_bytes


# ==========================================
# FASTAPI API ROUTES
# ==========================================

@app.get('/download-api')
async def get_audio_sample_and_recognize(
    id_or_url: Optional[str] = Query(None, alias="id_or_url"), 
    id: Optional[str] = Query(None)
):
    target_param = id_or_url or id
    if not target_param:
        raise HTTPException(status_code=400, detail="A valid video ID or URL is required.")

    # Parse out Video ID
    video_id = target_param.replace('dm-', '') if not target_param.startswith('http') else target_param.split('/')[-1].split('?')[0]
    target_url = f"https://www.dailymotion.com/video/{video_id}"

    # YT-DLP extraction setup
    ydl_opts = {
        'format': 'ba/b',
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'socket_timeout': 10,
        'nocheckcertificate': True,
        'geo_bypass': True,
        'external_downloader_args': ['-loglevel', 'panic'],
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
    }

    # Safely attach ImpersonateTarget if curl_cffi is available
    try:
        ydl_opts['impersonate'] = ImpersonateTarget.from_str('chrome')
    except Exception as imp_err:
        print(f"Impersonate target fallback (using standard HTTP): {imp_err}")

    try:
        def extract_info():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(target_url, download=False)
                
        info = await asyncio.to_thread(extract_info)
        
        if not info:
            raise HTTPException(status_code=404, detail="Could not retrieve video info.")

        audio_url = info.get('url')
        if not audio_url and info.get('formats'):
            audio_url = info.get('formats')[0].get('url')

        duration = info.get('duration')
        start_time = str(max(0, int(duration / 2) - 5)) if duration else '0'

    except Exception as e:
        error_detail = str(e).strip() or repr(e)
        raise HTTPException(status_code=500, detail=f"Failed to extract stream: {error_detail}")

    if not audio_url:
        raise HTTPException(status_code=503, detail="No playable audio stream found.")

    # Slice 10-second middle section using non-blocking FFmpeg process
    try:
        ffmpeg_cmd = [
            'ffmpeg', '-ss', start_time, '-i', audio_url,
            '-t', '10', '-vn', '-acodec', 'pcm_s16le',
            '-ar', '44100', '-ac', '2', '-f', 'wav', 'pipe:1'
        ]
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        normalized_wav_bytes, _ = await proc.communicate()

        if not normalized_wav_bytes:
            raise HTTPException(status_code=500, detail="FFmpeg slice failed to produce data.")

        recognition_result = await recognize_audio_bytes(normalized_wav_bytes)

        if recognition_result and 'track' in recognition_result:
            track_data = recognition_result['track']
            return {
                "success": True,
                "song_name": track_data.get('title'),
                "artist": track_data.get('subtitle'),
                "shazam_url": track_data.get('url')
            }
        else:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "No match found in the Shazam registry from the middle of this video."}
            )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal automation failed: {str(e)}")


@app.post('/shazam-io')
async def shazam_route(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file selected")

    try:
        incoming_bytes = await file.read()
        normalized_wav_bytes = await normalize_audio_with_ffmpeg(incoming_bytes)
        recognition_result = await recognize_audio_bytes(normalized_wav_bytes)

        if recognition_result and 'track' in recognition_result:
            track_data = recognition_result['track']
            return {
                "success": True,
                "song_name": track_data.get('title'),
                "artist": track_data.get('subtitle'),
                "shazam_url": track_data.get('url')
            }
        else:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "No match found in the Shazam registry."}
            )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal process failed: {str(e)}")


# ==========================================
# LOCAL TEST RUNNER
# ==========================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000, log_level="info")
