FROM python:3.10-slim

# Prevent Python from writing .pyc files and enable unbuffered logging for speed
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install FFmpeg and libjemalloc for high-performance memory allocation
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libjemalloc2 \
    && rm -rf /var/lib/apt-get/lists/*

# Use jemalloc as the default memory allocator
ENV LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2

WORKDIR /app

# Copy and install dependencies first (caches layer for fast rebuilds)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

EXPOSE 10000

# Run Uvicorn with optimized HTTP loop
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000", "--loop", "uvloop"]
