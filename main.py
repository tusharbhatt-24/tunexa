"""
Tunexa Backend
FastAPI server — run with: uvicorn main:app --reload --port 8000
"""

import os, re, uuid, time, json, asyncio, threading, shutil, urllib.request, urllib.parse
from pathlib import Path
from typing import Optional, List
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env if present

from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Resolve static-ffmpeg if installed (makes ffmpeg/ffprobe immediately available without external installs)
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

# Resolve yt-dlp binary path (works even if not on system PATH)
import sys as _sys
_YTDLP_BIN = str(Path(_sys.executable).parent / 'yt-dlp')
if not Path(_YTDLP_BIN).exists():
    _YTDLP_BIN = 'yt-dlp'  # fall back to PATH

# Resolve ffmpeg
import shutil as _shutil
_FFMPEG_BIN = _shutil.which('ffmpeg') or _shutil.which('/opt/homebrew/bin/ffmpeg') or None

# ── Optional deps (install as needed) ─────────
try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth, SpotifyClientCredentials
    SPOTIPY_OK = True
except ImportError:
    SPOTIPY_OK = False

try:
    from ytmusicapi import YTMusic
    YTMUSIC_OK = True
except ImportError:
    YTMUSIC_OK = False

try:
    from googleapiclient.discovery import build as yt_build
    from google.oauth2.credentials import Credentials as GCredentials
    GOOGLE_API_OK = True
except ImportError:
    GOOGLE_API_OK = False

# ── CONFIG (set via env vars) ──────────────────
SPOTIFY_CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI  = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8000/api/oauth/spotify/callback")
GOOGLE_CLIENT_ID      = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET  = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI   = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/oauth/google/callback")
FRONTEND_URL          = os.getenv("FRONTEND_URL", "http://localhost:3000")
DOWNLOAD_DIR          = Path(os.getenv("DOWNLOAD_DIR", "/tmp/tunexa"))
JOB_TTL_SECS          = 3600  # 1 hour

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── IN-MEMORY JOB STORE ────────────────────────
jobs: dict[str, dict] = {}
sessions: dict[str, dict] = {}  # token → session data (never persisted)

# ── LIFESPAN ───────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    cleaner = threading.Thread(target=cleanup_loop, daemon=True)
    cleaner.start()
    yield

app = FastAPI(title="Tunexa API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)

# Serve frontend static files at /ui/*  (eliminates all CORS issues)
_STATIC_DIR = Path(__file__).parent
app.mount('/ui', StaticFiles(directory=str(_STATIC_DIR), html=True), name='static')

from fastapi.responses import RedirectResponse
@app.get('/')
def root():
    return RedirectResponse('/ui/ytdl.html')

# ── MODELS ────────────────────────────────────
class FetchRequest(BaseModel):
    url: str

class UnifiedTrack(BaseModel):
    title: str
    artist: str
    isrc: Optional[str] = None
    duration: Optional[int] = None  # seconds

class TransferTrack(BaseModel):
    title: str
    artist: str
    isrc: Optional[str] = None
    duration: Optional[int] = None
    dest_id: Optional[str] = None

class CreatePlaylistRequest(BaseModel):
    playlist_name: str
    destination: str
    tracks: List[TransferTrack]
    oauth_token: Optional[str] = None

class MP3JobRequest(BaseModel):
    playlist_name: str
    tracks: List[TransferTrack]
    destination: str = "mp3"

class SearchRequest(BaseModel):
    query: str
    platform: str

# ── HELPERS ───────────────────────────────────
def detect_platform(url: str) -> str:
    if re.search(r"open\.spotify\.com/playlist/", url):
        return "spotify"
    if re.search(r"(music\.youtube\.com|youtube\.com)/playlist\?list=", url):
        return "youtube"
    return ""

def get_token(authorization: Optional[str] = Header(default=None)) -> Optional[str]:
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return None

def require_token(token: Optional[str] = Depends(get_token)):
    if not token:
        raise HTTPException(status_code=401, detail="OAuth token required")
    return token

# ── ROUTE: HEALTH ──────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "spotipy": SPOTIPY_OK, "ytmusicapi": YTMUSIC_OK}

# ── ROUTE: SETTINGS ────────────────────────────
class SpotifySettingsRequest(BaseModel):
    client_id: str
    client_secret: str

@app.get("/api/settings/spotify")
def get_spotify_settings():
    masked_secret = ""
    if SPOTIFY_CLIENT_SECRET:
        if len(SPOTIFY_CLIENT_SECRET) > 4:
            masked_secret = SPOTIFY_CLIENT_SECRET[:4] + "*" * (len(SPOTIFY_CLIENT_SECRET) - 4)
        else:
            masked_secret = "*" * len(SPOTIFY_CLIENT_SECRET)
    return {
        "client_id": SPOTIFY_CLIENT_ID,
        "client_secret_masked": masked_secret,
        "is_configured": bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)
    }

@app.post("/api/settings/spotify")
def save_spotify_settings(req: SpotifySettingsRequest):
    global SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
    env_path = Path(__file__).parent / ".env"
    lines = []
    if env_path.exists():
        with open(env_path, "r") as f:
            lines = f.readlines()
            
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("SPOTIFY_CLIENT_ID=") and not stripped.startswith("SPOTIFY_CLIENT_SECRET="):
            new_lines.append(line)
            
    new_lines.append(f"SPOTIFY_CLIENT_ID={req.client_id}\n")
    new_lines.append(f"SPOTIFY_CLIENT_SECRET={req.client_secret}\n")
    
    with open(env_path, "w") as f:
        f.writelines(new_lines)
        
    # Reload environment variables
    load_dotenv(env_path, override=True)
    SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
    SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
    
    return {"status": "success", "message": "Spotify API credentials updated successfully"}

# ── ROUTE: FETCH PLAYLIST ──────────────────────
@app.post("/api/fetch-playlist")
def fetch_playlist(req: FetchRequest):
    platform = detect_platform(req.url)
    if not platform:
        raise HTTPException(400, "URL not supported. Paste a Spotify or YouTube Music playlist URL.")

    if platform == "spotify":
        return fetch_spotify_playlist(req.url)
    return fetch_youtube_playlist(req.url)

def fetch_spotify_track_public(url: str) -> dict:
    """Fetch Spotify track metadata publicly using the embed page JSON payload."""
    try:
        match = re.search(r"track/([A-Za-z0-9]+)", url)
        if not match:
            raise HTTPException(400, "Cannot extract track ID from URL")
        track_id = match.group(1)
        
        embed_url = f"https://open.spotify.com/embed/track/{track_id}"
        req = urllib.request.Request(
            embed_url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
            }
        )
        
        with urllib.request.urlopen(req) as r:
            html = r.read().decode('utf-8')
            
        json_match = re.search(r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>', html, re.DOTALL)
        if not json_match:
            raise ValueError("Could not find NEXT_DATA block in Spotify embed HTML")
            
        data = json.loads(json_match.group(1))
        entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
        if not entity or entity.get("type") != "track":
            raise ValueError("Invalid or missing track entity in public response")
            
        title = entity.get("title") or entity.get("name") or "Spotify Track"
        artists = ", ".join(a.get("name") for a in entity.get("artists", []))
        duration = entity.get("duration", 0) // 1000
        thumb = entity.get("visualIdentity", {}).get("image", [{}])[0].get("url")
        if not thumb:
            thumb = entity.get("coverArt", {}).get("sources", [{}])[0].get("url") or ""
            
        query = f"{title} {artists} audio"
        
        return {
            'is_playlist': False,
            'is_spotify': True,
            'title': title,
            'uploader': artists,
            'duration': duration,
            'view_count': None,
            'thumbnail': thumb,
            'video_id': f"spotify_query:{query}",
            'query': query
        }
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch public Spotify metadata: {str(e)}")


def fetch_spotify_playlist_public(url: str) -> dict:
    """Fetch Spotify playlist metadata publicly using the embed page JSON payload."""
    try:
        match = re.search(r"playlist/([A-Za-z0-9]+)", url)
        if not match:
            raise HTTPException(400, "Cannot extract playlist ID from URL")
        playlist_id = match.group(1)
        
        embed_url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
        req = urllib.request.Request(
            embed_url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
            }
        )
        
        with urllib.request.urlopen(req) as r:
            html = r.read().decode('utf-8')
            
        json_match = re.search(r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>', html, re.DOTALL)
        if not json_match:
            raise ValueError("Could not find NEXT_DATA block in Spotify embed HTML")
            
        data = json.loads(json_match.group(1))
        entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
        if not entity:
            raise ValueError("Playlist entity not found in public response")
            
        name = entity.get("title") or entity.get("name") or "Spotify Playlist"
        tracks_data = entity.get("trackList", [])
        
        tracks = []
        for t in tracks_data:
            title = t.get("title") or "Unknown Title"
            artist = t.get("subtitle") or "Spotify Artist"
            duration_ms = t.get("duration", 180000)
            
            tracks.append({
                "title": title,
                "artist": artist,
                "isrc": None,
                "duration": duration_ms // 1000
            })
            
        thumb = entity.get("coverArt", {}).get("sources", [{}])[0].get("url") or ""
        
        return {"name": name, "tracks": tracks, "platform": "spotify", "thumbnail": thumb}
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch public Spotify playlist: {str(e)}")


def fetch_spotify_playlist(url: str) -> dict:
    if not SPOTIPY_OK:
        raise HTTPException(503, "spotipy not installed on server")
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return fetch_spotify_playlist_public(url)

    try:
        match = re.search(r"playlist/([A-Za-z0-9]+)", url)
        if not match:
            raise HTTPException(400, "Cannot extract playlist ID from URL")
        playlist_id = match.group(1)

        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
        ))

        pl = sp.playlist(playlist_id)
        name = pl.get("name", "Untitled Playlist")
        tracks = []
        results = pl["tracks"]

        while results:
            for item in results.get("items", []):
                t = item.get("track")
                if not t or t.get("type") != "track":
                    continue
                isrc = None
                em = t.get("external_ids", {})
                if em:
                    isrc = em.get("isrc")
                artists = ", ".join(a["name"] for a in t.get("artists", []))
                duration_ms = t.get("duration_ms", 0)
                tracks.append(UnifiedTrack(
                    title=t.get("name", ""),
                    artist=artists,
                    isrc=isrc,
                    duration=duration_ms // 1000,
                ).dict())
            results = sp.next(results) if results.get("next") else None

        return {"name": name, "tracks": tracks, "platform": "spotify"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Spotify API error: {str(e)}")

def fetch_youtube_playlist(url: str) -> dict:
    if not YTMUSIC_OK:
        raise HTTPException(503, "ytmusicapi not installed on server")

    try:
        match = re.search(r"list=([A-Za-z0-9_\-]+)", url)
        if not match:
            raise HTTPException(400, "Cannot extract playlist ID from URL")
        playlist_id = match.group(1)

        ytm = YTMusic()
        pl = ytm.get_playlist(playlist_id, limit=None)
        name = pl.get("title", "Untitled Playlist")
        tracks = []
        for t in pl.get("tracks", []):
            artists = ", ".join(a.get("name", "") for a in t.get("artists", []))
            dur = t.get("duration_seconds") or 0
            tracks.append(UnifiedTrack(
                title=t.get("title", ""),
                artist=artists,
                isrc=None,  # ytmusicapi does not expose ISRC
                duration=dur,
            ).dict())

        return {"name": name, "tracks": tracks, "platform": "youtube"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"YouTube Music error: {str(e)}")

# ── ROUTE: SEARCH ──────────────────────────────
@app.post("/api/search")
def search_tracks(req: SearchRequest):
    if req.platform == "spotify":
        return search_spotify(req.query)
    return search_youtube(req.query)

def search_spotify(query: str) -> dict:
    if not SPOTIPY_OK or not SPOTIFY_CLIENT_ID:
        return {"results": _mock_search(query)}
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET))
        res = sp.search(q=query, type="track", limit=10)
        results = []
        for t in res["tracks"]["items"]:
            artists = ", ".join(a["name"] for a in t["artists"])
            results.append({"id": t["id"], "title": t["name"],
                            "artist": artists, "duration": t["duration_ms"] // 1000})
        return {"results": results}
    except Exception as e:
        raise HTTPException(502, f"Spotify search error: {str(e)}")

def search_youtube(query: str) -> dict:
    if not YTMUSIC_OK:
        return {"results": _mock_search(query)}
    try:
        ytm = YTMusic()
        res = ytm.search(query, filter="songs", limit=10)
        results = []
        for t in res:
            artists = ", ".join(a.get("name", "") for a in t.get("artists", []))
            results.append({"id": t.get("videoId", ""), "title": t.get("title", ""),
                            "artist": artists, "duration": t.get("duration_seconds") or 0})
        return {"results": results}
    except Exception as e:
        raise HTTPException(502, f"YouTube search error: {str(e)}")

def _mock_search(query: str) -> list:
    return [{"id": f"mock_{i}", "title": f"{query} – Result {i+1}",
             "artist": "Unknown Artist", "duration": 200} for i in range(5)]

# ── ROUTE: OAUTH URLs ──────────────────────────
@app.get("/api/oauth/spotify/url")
def spotify_oauth_url():
    if not SPOTIFY_CLIENT_ID:
        raise HTTPException(503, "Spotify credentials not configured")
    scopes = "playlist-read-private playlist-modify-public playlist-modify-private"
    oauth = SpotifyOAuth(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET,
                         redirect_uri=SPOTIFY_REDIRECT_URI, scope=scopes)
    return {"url": oauth.get_authorize_url()}

@app.get("/api/oauth/google/url")
def google_oauth_url():
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google credentials not configured")
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        {"web": {"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
                 "redirect_uris": [GOOGLE_REDIRECT_URI],
                 "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                 "token_uri": "https://oauth2.googleapis.com/token"}},
        scopes=["https://www.googleapis.com/auth/youtube"],
        redirect_uri=GOOGLE_REDIRECT_URI,
    )
    url, _ = flow.authorization_url(prompt="consent")
    return {"url": url}

# ── ROUTE: OAUTH CALLBACKS ─────────────────────
@app.get("/api/oauth/spotify/callback")
def spotify_callback(code: str):
    oauth = SpotifyOAuth(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET,
                         redirect_uri=SPOTIFY_REDIRECT_URI)
    token_info = oauth.get_access_token(code)
    token = token_info["access_token"]
    # Return HTML that posts token to opener and closes
    return _oauth_success_page(token)

@app.get("/api/oauth/google/callback")
def google_callback(code: str):
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        {"web": {"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
                 "redirect_uris": [GOOGLE_REDIRECT_URI],
                 "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                 "token_uri": "https://oauth2.googleapis.com/token"}},
        scopes=["https://www.googleapis.com/auth/youtube"],
        redirect_uri=GOOGLE_REDIRECT_URI,
    )
    flow.fetch_token(code=code)
    token = flow.credentials.token
    return _oauth_success_page(token)

from fastapi.responses import HTMLResponse
def _oauth_success_page(token: str) -> HTMLResponse:
    html = f"""<!DOCTYPE html><html><body><script>
window.opener && window.opener.postMessage({{type:'oauth_token',token:{json.dumps(token)}}}, '*');
window.close();
</script><p>Authenticated! You may close this window.</p></body></html>"""
    return HTMLResponse(html)

# ── ROUTE: CREATE PLAYLIST ─────────────────────
@app.post("/api/create-playlist")
def create_playlist(req: CreatePlaylistRequest):
    if req.destination == "spotify":
        return create_spotify_playlist(req)
    elif req.destination == "youtube":
        return create_youtube_playlist(req)
    raise HTTPException(400, "Invalid destination")

def create_spotify_playlist(req: CreatePlaylistRequest) -> dict:
    if not SPOTIPY_OK:
        raise HTTPException(503, "spotipy not installed")
    token = req.oauth_token
    if not token:
        raise HTTPException(401, "Spotify OAuth token required")
    try:
        sp = spotipy.Spotify(auth=token)
        user_id = sp.me()["id"]
        pl = sp.user_playlist_create(user_id, req.playlist_name, public=False)
        pl_id = pl["id"]

        track_ids = [t.dest_id for t in req.tracks if t.dest_id]
        # Chunk into 100
        for i in range(0, len(track_ids), 100):
            chunk = track_ids[i:i+100]
            sp.playlist_add_items(pl_id, [f"spotify:track:{tid}" for tid in chunk])

        return {"playlist_url": pl["external_urls"]["spotify"], "failed": []}
    except Exception as e:
        raise HTTPException(502, f"Spotify error: {str(e)}")

def create_youtube_playlist(req: CreatePlaylistRequest) -> dict:
    if not GOOGLE_API_OK:
        raise HTTPException(503, "google-api-python-client not installed")
    token = req.oauth_token
    if not token:
        raise HTTPException(401, "Google OAuth token required")
    try:
        creds = GCredentials(token=token)
        yt = yt_build("youtube", "v3", credentials=creds)

        pl = yt.playlists().insert(part="snippet,status",
            body={"snippet": {"title": req.playlist_name}, "status": {"privacyStatus": "private"}}).execute()
        pl_id = pl["id"]

        failed = []
        for t in req.tracks:
            vid_id = t.dest_id
            if not vid_id:
                failed.append({"title": t.title, "artist": t.artist})
                continue
            try:
                yt.playlistItems().insert(part="snippet",
                    body={"snippet": {"playlistId": pl_id,
                                      "resourceId": {"kind": "youtube#video", "videoId": vid_id}}}).execute()
            except Exception as e:
                err_str = str(e)
                if "quotaExceeded" in err_str:
                    raise HTTPException(429, "YouTube Data API daily quota exceeded")
                failed.append({"title": t.title, "artist": t.artist})

        playlist_url = f"https://music.youtube.com/playlist?list={pl_id}"
        return {"playlist_url": playlist_url, "failed": failed}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"YouTube API error: {str(e)}")

# ── ROUTE: MP3 JOB ────────────────────────────
@app.post("/api/jobs/mp3")
def create_mp3_job(req: MP3JobRequest):
    job_id = str(uuid.uuid4())
    job_dir = DOWNLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    jobs[job_id] = {
        "id": job_id, "status": "queued", "progress": 0,
        "total": len(req.tracks), "current_track": None,
        "download_url": None, "failed_tracks": [],
        "created_at": time.time(), "dir": str(job_dir),
        "playlist_name": req.playlist_name,
    }

    thread = threading.Thread(target=run_mp3_job, args=(job_id, req.tracks, job_dir), daemon=True)
    thread.start()
    return {"job_id": job_id}

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    safe = {k: v for k, v in job.items() if k != "dir"}
    return safe

def run_mp3_job(job_id: str, tracks: List[TransferTrack], job_dir: Path):
    import subprocess
    job = jobs[job_id]
    job["status"] = "running"
    mp3_files = []

    for i, track in enumerate(tracks):
        if job["status"] == "failed":
            break
        query = f"{track.title} {track.artist}"
        job["current_track"] = query
        out_path = job_dir / f"{i+1:03d}_{_safe_name(track.title)}.mp3"

        try:
            cmd = [
                "yt-dlp",
                f"ytsearch1:{query}",
                "--extract-audio", "--audio-format", "mp3",
                "--audio-quality", "192K",
                "--output", str(out_path.with_suffix("")) + ".%(ext)s",
                "--no-playlist",
                "--quiet",
            ]
            subprocess.run(cmd, check=True, timeout=120)
            # yt-dlp may produce .webm before conversion — find the mp3
            mp3 = next(job_dir.glob(f"{i+1:03d}_{_safe_name(track.title)}*.mp3"), None)
            if mp3:
                mp3_files.append(mp3)
            else:
                job["failed_tracks"].append({"title": track.title, "artist": track.artist})
        except Exception as e:
            job["failed_tracks"].append({"title": track.title, "artist": track.artist})

        job["progress"] = int((i + 1) / len(tracks) * 90)

    # ZIP
    job["current_track"] = "Packaging ZIP…"
    zip_path = job_dir / f"{_safe_name(job['playlist_name'])}.zip"
    try:
        import zipfile
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in mp3_files:
                zf.write(f, f.name)
        job["download_url"] = f"/api/jobs/{job_id}/download"
        job["status"] = "complete"
        job["progress"] = 100
        job["completed_at"] = time.time()
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)

@app.get("/api/jobs/{job_id}/download")
def download_zip(job_id: str):
    job = jobs.get(job_id)
    if not job or job["status"] != "complete":
        raise HTTPException(404, "File not ready")
    job_dir = Path(job["dir"])
    zip_file = next(job_dir.glob("*.zip"), None)
    if not zip_file or not zip_file.exists():
        raise HTTPException(404, "ZIP file not found")
    return FileResponse(zip_file, media_type="application/zip", filename=zip_file.name)

# ── CLEANUP LOOP ──────────────────────────────
def cleanup_loop():
    while True:
        time.sleep(300)
        now = time.time()
        for job_id, job in list(jobs.items()):
            completed_at = job.get("completed_at")
            if completed_at and now - completed_at > JOB_TTL_SECS:
                job_dir = Path(job["dir"])
                if job_dir.exists():
                    shutil.rmtree(job_dir, ignore_errors=True)
                del jobs[job_id]

def _safe_name(s: str) -> str:
    return re.sub(r'[^\w\-_]', '_', s)[:50]


# ════════════════════════════════════════════════
# YOUTUBE DOWNLOADER ROUTES  (uses yt-dlp Python API)
# ════════════════════════════════════════════════

class YTDLInfoRequest(BaseModel):
    url: str

class YTDLDownloadRequest(BaseModel):
    url: str
    format: str = "mp3"
    quality: int = 192
    video_ids: Optional[List[str]] = None
    playlist_title: Optional[str] = None

ytdl_jobs: dict[str, dict] = {}

def _is_yt_url(url: str) -> bool:
    return bool(re.search(r"(youtube\.com/(watch|shorts|embed|playlist|music)|youtu\.be/|list=)", url))


def _is_spotify_url(url: str) -> bool:
    return bool(re.search(r"open\.spotify\.com/(track|playlist)/", url))


def fetch_spotify_track(url: str) -> dict:
    if not SPOTIPY_OK:
        raise HTTPException(503, "spotipy not installed on server")
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return fetch_spotify_track_public(url)
        
    try:
        match = re.search(r"track/([A-Za-z0-9]+)", url)
        if not match:
            raise HTTPException(400, "Cannot extract track ID from URL")
        track_id = match.group(1)
        
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
        ))
        
        t = sp.track(track_id)
        artists = ", ".join(a["name"] for a in t.get("artists", []))
        thumb = t["album"]["images"][0]["url"] if t["album"]["images"] else ""
        query = f"{t.get('name', '')} {artists} audio"
        
        return {
            'is_playlist': False,
            'is_spotify': True,
            'title': t.get("name", ""),
            'uploader': artists,
            'duration': t.get("duration_ms", 0) // 1000,
            'view_count': None,
            'thumbnail': thumb,
            'video_id': f"spotify_query:{query}",
            'query': query
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Spotify API error: {str(e)}")


@app.post("/api/ytdl/info")
def ytdl_info(req: YTDLInfoRequest):
    """Fetch metadata for a single video or a playlist using yt-dlp Python API."""
    import yt_dlp
    
    if _is_spotify_url(req.url):
        is_playlist = "playlist/" in req.url
        if is_playlist:
            data = fetch_spotify_playlist(req.url)
            entries = []
            for t in data.get("tracks", []):
                query = f"{t['title']} {t['artist']} audio"
                entries.append({
                    'title': f"{t['title']} - {t['artist']}",
                    'video_id': f"spotify_query:{query}",
                    'url': f"spotify_query:{query}",
                    'duration': t.get('duration', 180),
                    'uploader': t.get('artist', 'Spotify')
                })
            
            thumbnail = data.get("thumbnail", "")
            return {
                'is_playlist': True,
                'is_spotify': True,
                'title': data.get("name", "Spotify Playlist"),
                'uploader': 'Spotify',
                'video_count': len(entries),
                'thumbnail': thumbnail,
                'entries': entries
            }
        else:
            return fetch_spotify_track(req.url)

    if not _is_yt_url(req.url):
        raise HTTPException(400, "URL must be a YouTube or Spotify video/playlist link.")
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'skip_download': True,
    }
    if _FFMPEG_BIN:
        ydl_opts['ffmpeg_location'] = str(Path(_FFMPEG_BIN).parent)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
        if not info:
            raise HTTPException(422, "Could not extract video info.")

        is_playlist = info.get('_type') == 'playlist'

        if is_playlist:
            entries = []
            for entry in info.get('entries', []):
                if not entry:
                    continue
                entries.append({
                    'title': entry.get('title') or 'Untitled Video',
                    'video_id': entry.get('id') or entry.get('url'),
                    'url': f"https://www.youtube.com/watch?v={entry.get('id')}" if entry.get('id') else entry.get('url'),
                    'duration': entry.get('duration') or 0,
                    'uploader': entry.get('uploader') or entry.get('channel') or ''
                })
            
            thumb = info.get('thumbnail', '')
            if not thumb and entries:
                thumb = f"https://i.ytimg.com/vi/{entries[0]['video_id']}/hqdefault.jpg" if entries[0]['video_id'] else ''

            return {
                'is_playlist': True,
                'title': info.get('title', 'YouTube Playlist'),
                'uploader': info.get('uploader') or info.get('channel') or 'YouTube Uploader',
                'video_count': len(entries),
                'thumbnail': thumb,
                'entries': entries
            }
        else:
            thumb = info.get('thumbnail', '')
            thumbs = info.get('thumbnails', [])
            if thumbs:
                thumb = sorted(thumbs, key=lambda t: t.get('width', 0), reverse=True)[0].get('url', thumb)
            return {
                'is_playlist': False,
                'title':      info.get('title', ''),
                'uploader':   info.get('uploader') or info.get('channel', ''),
                'duration':   info.get('duration'),
                'view_count': info.get('view_count'),
                'thumbnail':  thumb,
                'video_id':   info.get('id', ''),
            }
    except HTTPException:
        raise
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if 'Private video' in msg:
            raise HTTPException(403, 'This video is private.')
        if 'age' in msg.lower():
            raise HTTPException(403, 'Age-restricted video — cannot be fetched without login.')
        raise HTTPException(422, f'yt-dlp: {msg[:200]}')
    except Exception as e:
        raise HTTPException(502, f'Unexpected error: {str(e)[:200]}')


@app.post("/api/ytdl/download")
def ytdl_download(req: YTDLDownloadRequest):
    """Enqueue a background download job (handles single video or list of videos)."""
    if not _is_yt_url(req.url) and not _is_spotify_url(req.url) and not req.url.startswith("spotify_query:"):
        raise HTTPException(400, 'URL must be a YouTube or Spotify link.')
    if req.format not in ('mp3', 'mp4'):
        raise HTTPException(400, "format must be 'mp3' or 'mp4'.")
    
    job_id = str(uuid.uuid4())
    job_dir = DOWNLOAD_DIR / 'ytdl' / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    
    is_playlist_job = bool(req.video_ids) and len(req.video_ids) > 1
    ytdl_jobs[job_id] = {
        'id': job_id, 'status': 'queued', 'progress': 0,
        'format': req.format, 'quality': req.quality,
        'status_msg': 'Queued', 'error': None,
        'file_path': None, 'file_size': None,
        'created_at': time.time(), 'dir': str(job_dir),
        'is_playlist': is_playlist_job
    }
    
    if is_playlist_job:
        t = threading.Thread(
            target=_run_ytdl_playlist_job,
            args=(job_id, req.video_ids, req.format, req.quality, req.playlist_title or "playlist", job_dir),
            daemon=True
        )
    else:
        url_to_download = req.video_ids[0] if (req.video_ids and len(req.video_ids) == 1) else req.url
        t = threading.Thread(
            target=_run_ytdl_job,
            args=(job_id, url_to_download, req.format, req.quality, job_dir),
            daemon=True
        )
    t.start()
    return {'job_id': job_id}


@app.get("/api/ytdl/jobs/{job_id}")
def ytdl_job_status(job_id: str):
    job = ytdl_jobs.get(job_id)
    if not job:
        raise HTTPException(404, 'Job not found.')
    return {k: v for k, v in job.items() if k not in ('dir', 'file_path')}


@app.get("/api/ytdl/jobs/{job_id}/file")
def ytdl_job_file(job_id: str):
    job = ytdl_jobs.get(job_id)
    if not job or job['status'] != 'complete':
        raise HTTPException(404, 'File not ready yet.')
    fp = Path(job['file_path'])
    if not fp.exists():
        raise HTTPException(404, 'File has been deleted (auto-cleanup).')
    media = 'audio/mpeg' if job['format'] == 'mp3' else 'video/mp4'
    if fp.suffix == '.zip':
        media = 'application/zip'
    return FileResponse(fp, media_type=media, filename=fp.name)


def _progress_hook(d: dict, job: dict):
    """yt-dlp progress hook — updates job state for a single video."""
    status = d.get('status')
    if status == 'downloading':
        pct_str = d.get('_percent_str', '0%').strip()
        try:
            raw = float(pct_str.replace('%', ''))
            job['progress'] = max(job['progress'], int(10 + raw * 0.8))
            job['status_msg'] = f"Downloading… {raw:.0f}%"
        except ValueError:
            pass
    elif status == 'finished':
        job['status_msg'] = 'Encoding…'
        job['progress'] = 92


def _run_ytdl_job(job_id: str, url: str, fmt: str, quality: int, job_dir: Path):
    """Run yt-dlp download using Python API — no subprocess PATH issues."""
    import yt_dlp
    
    job = ytdl_jobs[job_id]
    job['status'] = 'running'
    job['status_msg'] = 'Starting download…'
    job['progress'] = 5

    if url.startswith("spotify_query:"):
        query = url[len("spotify_query:"):]
        url = f"ytsearch1:{query}"
    elif _is_spotify_url(url):
        try:
            track_info = fetch_spotify_track(url)
            query = track_info.get("query")
            if not query:
                title = track_info.get("title", "Spotify Track")
                uploader = track_info.get("uploader", "")
                query = f"{title} {uploader} audio"
            url = f"ytsearch1:{query}"
        except Exception as e:
            job['status'] = 'failed'
            job['error'] = f"Failed to resolve Spotify track: {str(e)}"
            return

    out_tmpl = str(job_dir / '%(title)s.%(ext)s')
    ffmpeg_loc = str(Path(_FFMPEG_BIN).parent) if _FFMPEG_BIN else None

    if fmt == 'mp3':
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': out_tmpl,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [lambda d: _progress_hook(d, job)],
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': str(quality),
            }],
        }
    else:
        # MP4: pick best video up to requested height, merge with best audio
        fmt_sel = f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best'
        ydl_opts = {
            'format': fmt_sel,
            'outtmpl': out_tmpl,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'merge_output_format': 'mp4',
            'progress_hooks': [lambda d: _progress_hook(d, job)],
        }

    if ffmpeg_loc:
        ydl_opts['ffmpeg_location'] = ffmpeg_loc

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Find the output file
        files = [f for f in job_dir.glob('*') if f.is_file()]
        if not files:
            job['status'] = 'failed'
            job['error'] = 'No output file found after download.'
            return

        # Prefer the target format
        target_files = [f for f in files if f.suffix.lstrip('.') == fmt]
        out_file = target_files[0] if target_files else files[0]

        job['progress'] = 100
        job['status_msg'] = 'Complete!'
        job['status'] = 'complete'
        job['file_path'] = str(out_file)
        job['file_size'] = out_file.stat().st_size
        job['title'] = out_file.stem
        job['completed_at'] = time.time()

    except yt_dlp.utils.DownloadError as e:
        job['status'] = 'failed'
        msg = str(e)
        if 'ffmpeg' in msg.lower() or 'ffprobe' in msg.lower():
            job['error'] = 'ffmpeg is not installed properly.'
        else:
            job['error'] = msg[:300]
    except Exception as e:
        job['status'] = 'failed'
        job['error'] = str(e)[:300]


def _run_ytdl_playlist_job(job_id: str, video_ids: List[str], fmt: str, quality: int, playlist_title: str, job_dir: Path):
    """Downloads selected videos from a playlist in parallel or series and packs them in a ZIP file."""
    import yt_dlp
    import zipfile
    job = ytdl_jobs[job_id]
    job['status'] = 'running'
    job['status_msg'] = f'Starting playlist download (0/{len(video_ids)})…'
    job['progress'] = 5

    downloaded_files = []
    ffmpeg_loc = str(Path(_FFMPEG_BIN).parent) if _FFMPEG_BIN else None

    for idx, vid in enumerate(video_ids):
        if job['status'] == 'failed':
            break

        if vid.startswith("spotify_query:"):
            query = vid[len("spotify_query:"):]
            video_url = f"ytsearch1:{query}"
        else:
            video_url = f"https://www.youtube.com/watch?v={vid}"
        job['status_msg'] = f"Downloading {idx+1}/{len(video_ids)}: Starting…"
        
        out_tmpl = str(job_dir / f"{idx+1:03d}_%(title)s.%(ext)s")

        if fmt == 'mp3':
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': out_tmpl,
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': str(quality),
                }],
            }
        else:
            fmt_sel = f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best'
            ydl_opts = {
                'format': fmt_sel,
                'outtmpl': out_tmpl,
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'merge_output_format': 'mp4',
            }

        if ffmpeg_loc:
            ydl_opts['ffmpeg_location'] = ffmpeg_loc

        def local_progress_hook(d: dict, job=job, idx=idx, total=len(video_ids)):
            if d.get('status') == 'downloading':
                pct_str = d.get('_percent_str', '0%').strip()
                try:
                    raw = float(pct_str.replace('%', ''))
                    base = (idx / total) * 90
                    contrib = (raw / 100) * (90 / total)
                    job['progress'] = max(job['progress'], int(base + contrib))
                    job['status_msg'] = f"Downloading video {idx+1}/{total} ({raw:.0f}%)"
                except ValueError:
                    pass

        ydl_opts['progress_hooks'] = [local_progress_hook]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

            # Locate the output file for this track
            files = [f for f in job_dir.glob(f"{idx+1:03d}_*") if f.is_file()]
            if files:
                target_files = [f for f in files if f.suffix.lstrip('.') == fmt]
                out_file = target_files[0] if target_files else files[0]
                downloaded_files.append(out_file)
        except Exception:
            # Continue on error so we get the rest of the playlist
            continue

    if not downloaded_files:
        job['status'] = 'failed'
        job['error'] = 'No videos were successfully downloaded from the playlist.'
        return

    # ZIP packing
    job['status_msg'] = 'Packaging ZIP archive…'
    job['progress'] = 95
    zip_name = f"{_safe_name(playlist_title or 'playlist')}.zip"
    zip_path = job_dir / zip_name

    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in downloaded_files:
                zf.write(f, f.name)

        job['progress'] = 100
        job['status_msg'] = 'Complete!'
        job['status'] = 'complete'
        job['file_path'] = str(zip_path)
        job['file_size'] = zip_path.stat().st_size
        job['title'] = playlist_title or "playlist"
        job['completed_at'] = time.time()
    except Exception as e:
        job['status'] = 'failed'
        job['error'] = f"Failed to package ZIP: {str(e)}"

