import os
import re
import sys
import asyncio
from typing import Optional

# Web Framework & Server
from fastapi import FastAPI, UploadFile, File, Query, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

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
app = FastAPI(title="Maximum Speed Audio Recognition Engine")

# Pre-instantiate Shazam engine globally for zero re-initialization lag
shazam_engine = Shazam()

# ==========================================
# PLATFORM STUBS & HELPER FUNCTIONS
# ==========================================

@spaces.GPU
def dummy_gpu_trigger():
    return "Core Status: Active"


async def recognize_audio_bytes(file_bytes: bytes) -> dict:
    return await shazam_engine.recognize(file_bytes)


async def normalize_audio_with_ffmpeg(input_bytes: bytes) -> bytes:
    try:
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg', '-fflags', '+nobuffer', '-probesize', '32', '-analyzeduration', '0',
            '-i', 'pipe:0', '-f', 'wav', '-acodec', 'pcm_s16le', '-ar', '44100', 'pipe:1',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate(input=input_bytes)
        return stdout if proc.returncode == 0 else input_bytes
    except Exception:
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

    # MAX-SPEED YT-DLP OPTIONS
    ydl_opts = {
        'format': 'ba[ext=m4a]/ba/b',
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'noplaylist': True,
        'check_formats': False,
        'nocheckcertificate': True,
        'geo_bypass': True,
        'youtube_include_dash_manifest': False,
        'youtube_include_hls_manifest': False,
        'external_downloader_args': ['-loglevel', 'panic'],
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
    }

    try:
        ydl_opts['impersonate'] = ImpersonateTarget.from_str('chrome')
    except Exception:
        pass

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

        # Calculate exact midpoint start time
        duration = info.get('duration')
        start_time = str(max(0, int(duration / 2) - 2)) if duration else '0'

    except Exception as e:
        error_detail = str(e).strip() or repr(e)
        raise HTTPException(status_code=500, detail=f"Failed to extract stream: {error_detail}")

    if not audio_url:
        raise HTTPException(status_code=503, detail="No playable audio stream found.")

    # FAST INPUT-SEEKING FFMPEG PIPE (-ss placed before -i for fast jump)
    try:
        ffmpeg_cmd = [
            'ffmpeg',
            '-fflags', '+nobuffer',
            '-probesize', '32',
            '-analyzeduration', '0',
            '-ss', start_time,      # Jump directly to middle before loading stream
            '-i', audio_url,
            '-t', '5',              # 5-second slice is optimal for Shazam accuracy
            '-vn',
            '-acodec', 'pcm_s16le',
            '-ar', '44100',
            '-ac', '2',
            '-f', 'wav',
            'pipe:1'
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
                content={"success": False, "message": "No match found in the Shazam registry."}
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000, log_level="info")
