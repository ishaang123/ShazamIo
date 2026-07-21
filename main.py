import os
import sys
import importlib
import subprocess
import threading
import time
import requests
from flask import Flask, request, jsonify

# Initialize Flask App
app = Flask(__name__)

# Global Cache for Authentication
CACHE = {
    "access_token": None
}

def update_ytdlp_loop():
    # Wait 5 hours before running the first update check
    time.sleep(18000)
    
    while True:
        print("Updating yt-dlp in the background...", flush=True)
        try:
            # 1. Use sys.executable to ensure it targets the correct Python environment
            # 2. Use --no-cache-dir to guarantee it pulls the newest code from PyPI
            subprocess.check_call([
                sys.executable, "-m", "pip", 
                "install", "--no-cache-dir", "--upgrade", "yt-dlp"
            ])
            
            # 3. Force Python to clear the old memory space and reload the new yt-dlp code
            global yt_dlp
            yt_dlp = importlib.reload(yt_dlp)
            print("yt-dlp successfully updated and reloaded in memory!", flush=True)
            
        except Exception as e:
            print(f"Background update failed: {e}", flush=True)
            
        # Sleep for another 5 hours
        time.sleep(18000)

# This starts the 5-hour timer in the background as soon as Flask boots up
threading.Thread(target=update_ytdlp_loop, daemon=True).start()


def get_dailymotion_token():
    if CACHE["access_token"]:
        return CACHE["access_token"]

    auth_url = "https://graphql.api.dailymotion.com/oauth/token"
    payload = {
        "client_id": "f1a362d288c1b98099c7",
        "client_secret": "eea605b96e01c796ff369935357eca920c5da4c5",
        "grant_type": "client_credentials"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    try:
        response = requests.post(auth_url, data=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            token_data = response.json()
            CACHE["access_token"] = token_data.get("access_token")
            return CACHE["access_token"]
    except Exception as e:
        print(f"Failed to generate client credentials token: {e}")
    return None


@app.route('/api/scrape-dailymotion-comments')
def scrape_dailymotion_comments():
    video_id = request.args.get('watch')
    if not video_id:
        response = jsonify({"error": "Missing watch parameter"})
        response.headers["Access-Control-Allow-Origin"] = "*" # <-- Allow CORS
        return response, 400

    clean_id = video_id.replace('dm-', '')

    token = get_dailymotion_token()
    if not token:
        response = jsonify({"error": "Failed to authenticate with Dailymotion backend"})
        response.headers["Access-Control-Allow-Origin"] = "*" # <-- Allow CORS
        return response, 500

    graphql_url = "https://graphql.api.dailymotion.com"

    graphql_query = """
    query($videoId: String!) {
        video(xid: $videoId) {
            comments(first: 20) {
                edges {
                    node {
                        id
                        text
                        creator {
                            username
                            name
                        }
                    }
                }
            }
        }
    }
    """

    payload = {
        "query": graphql_query,
        "variables": {
            "videoId": clean_id
        }
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "Origin": "https://www.dailymotion.com",
        "Referer": f"https://www.dailymotion.com/video/{clean_id}"
    }

    try:
        res = requests.post(
            graphql_url,
            json=payload,
            headers=headers,
            timeout=10
        )

        if res.status_code == 401:
            CACHE["access_token"] = None
            response = jsonify({"error": "Token expired, please refresh endpoint request"})
            response.headers["Access-Control-Allow-Origin"] = "*" # <-- Allow CORS
            return response, 401

        if res.status_code != 200:
            response = jsonify({
                "comments": [],
                "debug": f"API responded with status code {res.status_code}",
                "server_response": res.text
            })
            response.headers["Access-Control-Allow-Origin"] = "*" # <-- Allow CORS
            return response, res.status_code

        res_data = res.json()

        if "errors" in res_data:
            response = jsonify({
                "comments": [],
                "debug": "GraphQL Engine Schema Failure",
                "details": res_data.get("errors")
            })
            response.headers["Access-Control-Allow-Origin"] = "*" # <-- Allow CORS
            return response, 400

        parsed_comments = []
        video_data = res_data.get("data", {}).get("video") or {}
        comment_edges = video_data.get("comments", {}).get("edges", [])

        for edge in comment_edges:
            node = edge.get("node", {})
            creator_node = node.get("creator") or {}

            display_name = creator_node.get("name") or creator_node.get("username") or "Dailymotion User"

            parsed_comments.append({
                "id": node.get("id") or "",
                "author": display_name,
                "text": node.get("text") or ""
            })

        # Final successful return tracking
        response = jsonify({"comments": parsed_comments})
        response.headers["Access-Control-Allow-Origin"] = "*" # <-- Allow CORS
        return response

    except Exception as e:
        response = jsonify({
            "error": "Failed to parse API runtime response",
            "details": str(e)
        })
        response.headers["Access-Control-Allow-Origin"] = "*" # <-- Allow CORS
        return response, 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
