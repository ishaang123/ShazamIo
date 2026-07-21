import os
import sys
import importlib
import subprocess
import threading
import time
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# Import yt_dlp so it can be dynamically reloaded later
import yt_dlp

app = Flask(__name__)
CORS(app)  # Handles Access-Control-Allow-Origin globally

CACHE = {
    "access_token": None
}

def update_ytdlp_loop():
    time.sleep(18000)
    while True:
        print("Updating yt-dlp in the background...", flush=True)
        try:
            subprocess.check_call([
                sys.executable, "-m", "pip", 
                "install", "--no-cache-dir", "--upgrade", "yt-dlp"
            ])
            global yt_dlp
            yt_dlp = importlib.reload(yt_dlp)
            print("yt-dlp successfully updated and reloaded!", flush=True)
        except Exception as e:
            print(f"Background update failed: {e}", flush=True)
        time.sleep(18000)

threading.Thread(target=update_ytdlp_loop, daemon=True).start()


def get_dailymotion_token():
    if CACHE["access_token"]:
        return CACHE["access_token"]

    auth_url = "https://graphql.api.dailymotion.com/oauth/token"
    
    # Credentials pulled securely from environment variables
    payload = {
        "client_id": os.getenv("DAILYMOTION_CLIENT_ID", "f1a362d288c1b98099c7"),
        "client_secret": os.getenv("DAILYMOTION_CLIENT_SECRET", "eea605b96e01c796ff369935357eca920c5da4c5"),
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
        return jsonify({"error": "Missing watch parameter"}), 400

    clean_id = video_id.replace('dm-', '')

    token = get_dailymotion_token()
    if not token:
        return jsonify({"error": "Failed to authenticate with Dailymotion backend"}), 500

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
        "variables": {"videoId": clean_id}
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
        res = requests.post(graphql_url, json=payload, headers=headers, timeout=10)

        if res.status_code == 401:
            CACHE["access_token"] = None
            return jsonify({"error": "Token expired, please refresh endpoint request"}), 401

        if res.status_code != 200:
            return jsonify({
                "comments": [],
                "debug": f"API responded with status code {res.status_code}",
                "server_response": res.text
            }), res.status_code

        res_data = res.json()

        if "errors" in res_data:
            return jsonify({
                "comments": [],
                "debug": "GraphQL Engine Schema Failure",
                "details": res_data.get("errors")
            }), 400

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

        return jsonify({"comments": parsed_comments})

    except Exception as e:
        return jsonify({
            "error": "Failed to parse API runtime response",
            "details": str(e)
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
