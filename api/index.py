import os
import re
import base64
import logging
import sys
from urllib.parse import quote
from typing import Dict, Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Spotify DL API", version="1.0.0")

# Environment (configure in Vercel)
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "d2f27b893fb64c3a97242d8a1e46c63c")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "8d31ddeef0614731be0e6cef6aebaad3")
SPOTIFY_AUTH_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

API_OWNER = "Stellar"
API_UPDATES = "@ApexServers"

class UrlRequest(BaseModel):
    url: str

def validate_spotify_url(url: str) -> str:
    if not url or not re.match(r"^https://open\.spotify\.com/track/[a-zA-Z0-9]+", url):
        logger.error("Invalid Spotify track URL")
        raise ValueError("Valid Spotify track URL required")
    return url

def extract_track_id(url: str) -> str:
    if re.match(r"^[a-zA-Z0-9]{22}$", url):
        return url
    match = re.search(r"spotify\.com/track/([a-zA-Z0-9]{22})", url)
    if match:
        return match.group(1)
    logger.error("Failed to extract track ID")
    raise ValueError("Invalid Spotify track ID or URL")

async def get_spotify_token() -> str:
    auth_string = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
    auth_b64 = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")
    headers = {
        "Authorization": f"Basic {auth_b64}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"grant_type": "client_credentials"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(SPOTIFY_AUTH_URL, headers=headers, data=data)
            r.raise_for_status()
            return r.json()["access_token"]
    except httpx.HTTPError as e:
        logger.error(f"Failed to get Spotify token: {e}")
        raise ValueError("Unable to authenticate with Spotify") from e

async def get_track_metadata(track_id: str) -> Dict[str, Any]:
    token = await get_spotify_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{SPOTIFY_API_BASE}/tracks/{track_id}", headers=headers)
            r.raise_for_status()
            track = r.json()
            return {
                "id": track["id"],
                "title": track["name"],
                "artists": [{"name": a["name"], "id": a["id"]} for a in track["artists"]],
                "album": {
                    "name": track["album"]["name"],
                    "id": track["album"]["id"],
                    "release_date": track["album"]["release_date"],
                },
                "duration": f"{track['duration_ms'] // 60000}:{(track['duration_ms'] % 60000) // 1000:02d}",
                "cover": track["album"]["images"][0]["url"] if track["album"]["images"] else None,
                "url": track["external_urls"]["spotify"],
                "isrc": track.get("external_ids", {}).get("isrc", "N/A"),
            }
    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch track metadata: {e}")
        raise ValueError("Unable to retrieve track data") from e

async def process_download(url: str) -> Dict[str, Any]:
    try:
        validated_url = validate_spotify_url(url)
        track_id = extract_track_id(validated_url)
        logger.info(f"Processing track ID: {track_id}")

        track_data = await get_track_metadata(track_id)
        logger.info(f"Retrieved metadata for track: {track_data['title']}")

        check_endpoint = f"https://spotmp3.app/api/check-direct-download?url={quote(validated_url)}"
        logger.info(f"Checking download availability: {check_endpoint}")

        async with httpx.AsyncClient(timeout=25) as client:
            check_response = await client.get(check_endpoint)
            check_response.raise_for_status()
            check_result = check_response.json()

        logger.info(f"Download check result: {check_result}")

        response_data = {
            "status": "success",
            "track": track_data,
            "download": None,
            "API_OWNER": API_OWNER,
            "API_UPDATES": API_UPDATES,
        }

        if check_result.get("cached"):
            download_link = f"https://spotmp3.app/api/direct-download?url={quote(validated_url)}"
            logger.info(f"Download link available: {download_link}")
            response_data["download"] = {"link": download_link}
        else:
            response_data["download"] = check_result

        return response_data

    except httpx.HTTPError as e:
        logger.error(f"Network error during download check: {e}")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "message": str(e), "API_OWNER": API_OWNER, "API_UPDATES": API_UPDATES},
        ) from e
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": str(e), "API_OWNER": API_OWNER, "API_UPDATES": API_UPDATES},
        ) from e
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "message": str(e), "API_OWNER": API_OWNER, "API_UPDATES": API_UPDATES},
        ) from e

# === Routes ===

@app.get("/", response_class=HTMLResponse)
async def landing():
    """Serve the violet docs/playground page."""
    here = os.path.dirname(__file__)
    path = os.path.join(here, "template", "index.html")
    try:
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        # inject current origin in curl examples at runtime via client-side script placeholder
        html = html.replace("{{ORIGIN}}", "")
        return HTMLResponse(content=html, status_code=200)
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>Docs page missing</h1><p>Place template at <code>api/template/index.html</code>.</p>",
            status_code=200,
        )

@app.get("/sp/dl")
async def download_get(url: str = Query(..., description="Spotify track URL")):
    return await process_download(url)

@app.post("/sp/dl")
async def download_post(request: UrlRequest):
    return await process_download(request.url)

@app.get("/sp/search")
async def search(q: str = Query(..., description="Search query")):
    if not q:
        logger.error("Search query missing")
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "message": "Query required",
                "example": "/sp/search?q=Song+Name",
                "API_OWNER": API_OWNER,
                "API_UPDATES": API_UPDATES,
            },
        )
    try:
        token = await get_spotify_token()
        headers = {"Authorization": f"Bearer {token}"}
        params = {"q": q, "type": "track", "limit": 5}
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{SPOTIFY_API_BASE}/search", headers=headers, params=params)
            r.raise_for_status()
            tracks = r.json()["tracks"]["items"]

        if not tracks:
            logger.info(f"No tracks found for query: {q}")
            raise HTTPException(
                status_code=404,
                detail={"status": "error", "message": "No tracks found", "API_OWNER": API_OWNER, "API_UPDATES": API_UPDATES},
            )

        response_data = [
            {
                "title": t["name"],
                "artist": ", ".join(a["name"] for a in t["artists"]),
                "id": t["id"],
                "url": t["external_urls"]["spotify"],
                "album": t["album"]["name"],
                "release_date": t["album"]["release_date"],
                "duration": f"{t['duration_ms'] // 60000}:{(t['duration_ms'] % 60000) // 1000:02d}",
                "cover": t["album"]["images"][0]["url"] if t["album"]["images"] else None,
            }
            for t in tracks
        ]
        logger.info(f"Found {len(response_data)} tracks for query: {q}")
        return {"status": "success", "results": response_data, "API_OWNER": API_OWNER, "API_UPDATES": API_UPDATES}

    except httpx.HTTPError as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "message": str(e), "API_OWNER": API_OWNER, "API_UPDATES": API_UPDATES},
        ) from e
    except Exception as e:
        logger.error(f"Unexpected search error: {e}")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "message": str(e), "API_OWNER": API_OWNER, "API_UPDATES": API_UPDATES},
        ) from e
