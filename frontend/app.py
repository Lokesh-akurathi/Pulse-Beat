import asyncio
import os
from typing import Optional
from dotenv import load_dotenv
import httpx
from quart import Quart, redirect, render_template, request, session, url_for

load_dotenv()

app = Quart(
    __name__,
    template_folder="templates",
    static_folder="static",
)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
API_TIMEOUT = int(os.getenv("API_TIMEOUT", "60"))
DEFAULT_USER_ID = int(os.getenv("DEFAULT_USER_ID", "1"))

# Shared persistent asynchronous HTTP client for connection pooling and high throughput
_http_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(API_TIMEOUT, connect=30.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )
    return _http_client


@app.after_serving
async def close_http_client():
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()


def _get_logged_in_user_id():
    user_id = session.get("user_id")
    if user_id is not None:
        return int(user_id)
    return None


def _require_user_id():
    user_id = _get_logged_in_user_id()
    if user_id is None:
        return None
    return user_id


async def _safe_get(path: str, params: Optional[dict] = None, fallback=None, timeout: int = API_TIMEOUT):
    client = get_http_client()
    headers = {}
    if "access_token" in session:
        headers["Authorization"] = f"Bearer {session['access_token']}"
    try:
        response = await client.get(f"{API_BASE_URL}{path}", params=params, headers=headers, timeout=timeout)
        if response.is_success:
            return response.json()
    except httpx.HTTPError:
        pass
    return fallback


async def _safe_post(path: str, json_data: Optional[dict] = None, timeout: int = API_TIMEOUT):
    """
    Execute an asynchronous POST request against the backend API.
    Returns: (data, error_message)
    """
    client = get_http_client()
    headers = {}
    if "access_token" in session:
        headers["Authorization"] = f"Bearer {session['access_token']}"
    try:
        response = await client.post(f"{API_BASE_URL}{path}", json=json_data, headers=headers, timeout=timeout)
        if response.is_success:
            return response.json(), None
        # Handle Render free-tier cold start gateway responses (502 Bad Gateway / 503 / 504)
        if response.status_code in (502, 503, 504):
            return None, "Backend service is waking up from sleep (Render cold start). Please wait ~30 seconds and try again."
        try:
            detail = response.json().get("detail", f"Request failed (HTTP {response.status_code})")
        except Exception:
            detail = f"Request failed (HTTP {response.status_code})"
        return None, detail
    except httpx.TimeoutException:
        return None, "Backend request timed out. The server is likely waking up from sleep; please try again in a few moments."
    except httpx.HTTPError:
        return None, "Unable to connect to the backend authentication service. Please verify the API server is reachable."


@app.route("/")
async def index():
    return redirect(url_for("login"))


@app.route("/home")
async def home():
    user_id = _require_user_id()
    if user_id is None:
        return redirect(url_for("login"))
    
    profile = await _safe_get(f"/api/profile/{user_id}", fallback={}) or {}
    profile.setdefault("user_id", user_id)
    profile.setdefault("name", "Guest User")
    profile.setdefault("email_id", "guest@example.com")
    profile.setdefault("subscription_type", "free")
    profile.setdefault("user_role", "user")
    profile.setdefault("wallet_balance", 0)
    
    # If user is an artist, fetch artist studio data
    if profile.get("user_role") == "artist":
        data = await _safe_get(f"/api/artist-studio/{user_id}", fallback={}) or {}
        data.setdefault("profile", profile)
        data.setdefault("recent_releases", [])
        data.setdefault("top_releases", [])
        data.setdefault("top_collaborators", [])
    else:
        # Regular user home
        data = await _safe_get("/api/home", params={"user_id": user_id}, fallback={}) or {}
        data.setdefault("profile", profile)
        data.setdefault("trending_songs", [])
        data.setdefault("recommendations", [])
        data.setdefault("playlists", [])
        data.setdefault("song_sales", [])
        data.setdefault("liked_songs", [])
        data.setdefault("followed_artists", [])
        data.setdefault("all_artists", [])
        data.setdefault("genres", [])
        data.setdefault("recent_songs", [])
    
    return await render_template(
        "index.html",
        api_base_url=API_BASE_URL,
        user_id=user_id,
        data=data,
        profile=profile,
    )


@app.route("/login", methods=["GET", "POST"])
async def login():
    if _get_logged_in_user_id() is not None:
        return redirect(url_for("home"))

    error = None
    success = None
    if request.args.get("registered") == "success":
        success = "Registration successful! Please log in with your new credentials."

    if request.method == "POST":
        form = await request.form
        email = form.get("email", "").strip()
        password = form.get("password", "").strip()
        
        # Authenticate asynchronously via FastAPI backend
        auth_response, error = await _safe_post("/api/auth/login", json_data={"email": email, "password": password})
        if auth_response and "access_token" in auth_response:
            session["user_id"] = auth_response["user_id"]
            session["access_token"] = auth_response["access_token"]
            return redirect(url_for("home"))

    return await render_template("login.html", error=error, success=success, api_base_url=API_BASE_URL)


@app.route("/register", methods=["GET", "POST"])
async def register():
    if _get_logged_in_user_id() is not None:
        return redirect(url_for("home"))

    error = None
    if request.method == "POST":
        form = await request.form
        name = form.get("name", "").strip()
        email = form.get("email", "").strip()
        password = form.get("password", "").strip()
        role = form.get("role", "").strip()
        nationality = form.get("nationality", "").strip() or "Unknown"

        if not name or not email or not password or not role:
            error = "All fields are required"
        else:
            reg_response, error = await _safe_post(
                "/api/auth/register",
                json_data={
                    "name": name,
                    "email": email,
                    "password": password,
                    "role": role,
                    "nationality": nationality if role == "artist" else "Unknown"
                }
            )
            if reg_response:
                return redirect(url_for("login", registered="success"))

    return await render_template("register.html", error=error, api_base_url=API_BASE_URL)


@app.route("/logout")
async def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/collection/<string:kind>")
async def collection_view(kind: str):
    user_id = _require_user_id()
    if user_id is None:
        return redirect(url_for("login"))
    normalized = kind.lower()
    if normalized not in {"sales", "likes", "downloads"}:
        normalized = "sales"
    
    if normalized == "sales":
        api_path = "/api/song-sales"
        label = "Bought Songs"
    elif normalized == "downloads":
        api_path = "/api/downloads"
        label = "Downloaded Songs"
    else:
        api_path = "/api/likes"
        label = "Favourites"
    
    # Fetch songs and profile concurrently
    songs, profile = await asyncio.gather(
        _safe_get(f"{api_path}/{user_id}", fallback=[]),
        _safe_get(f"/api/profile/{user_id}", fallback={}),
    )
    return await render_template(
        "collection.html",
        api_base_url=API_BASE_URL,
        user_id=user_id,
        profile=profile or {},
        collection_title=label,
        songs=songs or [],
        kind=normalized,
    )


@app.route("/followed-artists")
async def followed_artists_view():
    user_id = _require_user_id()
    if user_id is None:
        return redirect(url_for("login"))
    followed_artists, profile = await asyncio.gather(
        _safe_get(f"/api/followed-artists/{user_id}", fallback=[]),
        _safe_get(f"/api/profile/{user_id}", fallback={}),
    )
    return await render_template(
        "followed_artists.html",
        api_base_url=API_BASE_URL,
        user_id=user_id,
        profile=profile or {},
        artists=followed_artists or [],
    )


@app.route("/all-songs")
async def all_songs_view():
    user_id = _require_user_id()
    if user_id is None:
        return redirect(url_for("login"))
    profile = await _safe_get(f"/api/profile/{user_id}", fallback={}) or {}
    profile.setdefault("user_role", "user")
    
    if profile.get("user_role") == "artist":
        # Artists only see their own published songs
        studio_data = await _safe_get(f"/api/artist-studio/{user_id}", fallback={}) or {}
        songs = studio_data.get("songs", [])
    else:
        songs = await _safe_get("/api/songs/all", fallback=[]) or []
    
    return await render_template(
        "all_songs.html",
        api_base_url=API_BASE_URL,
        user_id=user_id,
        profile=profile,
        songs=songs,
    )


@app.route("/playlists")
async def playlists_view():
    user_id = _require_user_id()
    if user_id is None:
        return redirect(url_for("login"))
    
    profile, playlists = await asyncio.gather(
        _safe_get(f"/api/profile/{user_id}", fallback={}),
        _safe_get(f"/api/playlists/{user_id}", fallback=[]),
    )
    playlists = playlists or []
    
    if playlists:
        # Fetch preview tracks for all playlists concurrently in parallel
        tasks = [
            _safe_get(f"/api/playlists/{p['playlist_id']}/songs", params={"user_id": user_id}, fallback={})
            for p in playlists
        ]
        playlist_previews = await asyncio.gather(*tasks)
        for p, preview in zip(playlists, playlist_previews):
            p["top_songs"] = (preview or {}).get("songs", [])[:4]

    return await render_template(
        "playlists.html",
        api_base_url=API_BASE_URL,
        user_id=user_id,
        profile=profile or {},
        playlists=playlists,
    )


@app.route("/playlist/<int:playlist_id>")
async def playlist_view(playlist_id: int):
    user_id = _require_user_id()
    if user_id is None:
        return redirect(url_for("login"))
    payload, profile = await asyncio.gather(
        _safe_get(f"/api/playlists/{playlist_id}/songs", params={"user_id": user_id}, fallback={}),
        _safe_get(f"/api/profile/{user_id}", fallback={}),
    )
    payload = payload or {}
    playlist = payload.get(
        "playlist",
        {
            "playlist_id": playlist_id,
            "playlist_name": "Playlist",
            "created_date": "-",
            "song_count": 0,
        },
    )
    songs = payload.get("songs", [])
    return await render_template(
        "playlist.html",
        api_base_url=API_BASE_URL,
        user_id=user_id,
        profile=profile or {},
        playlist=playlist,
        songs=songs,
    )


@app.route("/artist/<int:artist_id>")
async def artist_view(artist_id: int):
    user_id = _require_user_id()
    if user_id is None:
        return redirect(url_for("login"))
    payload, profile, followed_artists = await asyncio.gather(
        _safe_get(f"/api/artists/{artist_id}", fallback={}),
        _safe_get(f"/api/profile/{user_id}", fallback={}),
        _safe_get(f"/api/followed-artists/{user_id}", fallback=[]),
    )
    payload = payload or {}
    artist = payload.get(
        "artist",
        {
            "artist_id": artist_id,
            "artist_name": "Artist",
            "nationality": "-",
            "num_followers": 0,
            "monetization_status": "notmonetized",
        },
    )
    songs = payload.get("songs", [])
    followed_artists = followed_artists or []
    is_following = any(a.get("artist_id") == artist_id for a in followed_artists)
    return await render_template(
        "artist.html",
        api_base_url=API_BASE_URL,
        user_id=user_id,
        profile=profile or {},
        artist=artist,
        songs=songs,
        is_following=is_following,
    )


@app.route("/genres")
async def genres_view():
    user_id = _require_user_id()
    if user_id is None:
        return redirect(url_for("login"))
    
    genres, profile = await asyncio.gather(
        _safe_get("/api/genres", fallback=[]),
        _safe_get(f"/api/profile/{user_id}", fallback={}),
    )
    
    return await render_template(
        "genres.html",
        api_base_url=API_BASE_URL,
        user_id=user_id,
        profile=profile or {},
        genres=genres or [],
    )


@app.route("/genre/<int:genre_id>")
async def genre_view(genre_id: int):
    user_id = _require_user_id()
    if user_id is None:
        return redirect(url_for("login"))
    
    all_genres, profile = await asyncio.gather(
        _safe_get("/api/genres", fallback=[]),
        _safe_get(f"/api/profile/{user_id}", fallback={}),
    )
    all_genres = all_genres or []
    profile = profile or {}
    genre = next((g for g in all_genres if g.get("genre_id") == genre_id), 
                 {"genre_id": genre_id, "genre_name": "Genre"})
    
    profile.setdefault("user_role", "user")

    if profile.get("user_role") == "artist":
        songs = await _safe_get(
            f"/api/genres/{genre_id}/songs",
            params={"user_id": user_id},
            fallback=[],
        ) or []
    else:
        songs = await _safe_get(f"/api/genres/{genre_id}/songs", fallback=[]) or []
    
    return await render_template(
        "genre.html",
        api_base_url=API_BASE_URL,
        user_id=user_id,
        profile=profile,
        genre=genre,
        songs=songs,
    )


@app.route("/artist-studio")
async def artist_studio_view():
    user_id = _require_user_id()
    if user_id is None:
        return redirect(url_for("login"))
    payload, profile = await asyncio.gather(
        _safe_get(f"/api/artist-studio/{user_id}", fallback=None),
        _safe_get(f"/api/profile/{user_id}", fallback={}),
    )
    profile = profile or {}
    edit_song = None
    edit_song_id_raw = request.args.get("edit_song_id")
    if edit_song_id_raw and profile.get("user_role") == "artist":
        try:
            edit_song_id = int(edit_song_id_raw)
        except (TypeError, ValueError):
            edit_song_id = None
        if edit_song_id is not None:
            edit_song = await _safe_get(
                f"/api/artist-studio/song/{edit_song_id}",
                params={"user_id": user_id},
                fallback=None,
            )
    if payload is None:
        payload = {
            "artist": {
                "artist_id": 0,
                "artist_name": profile.get("name", "Artist"),
                "nationality": "Unknown",
                "num_followers": 0,
                "monetization_status": "notmonetized",
            },
            "songs": [],
            "transactions": [],
            "summary": {"total_earned": 0, "transaction_count": 0},
            "collaborators": [],
            "genres": [],
            "sale_price": 29.0,
        }
    return await render_template(
        "artist_studio.html",
        api_base_url=API_BASE_URL,
        user_id=user_id,
        profile=profile,
        payload=payload,
        edit_song=edit_song,
    )


@app.route("/profile")
async def profile_view():
    user_id = _require_user_id()
    if user_id is None:
        return redirect(url_for("login"))
    profile = await _safe_get(f"/api/profile/{user_id}", fallback={}) or {}
    return await render_template(
        "profile.html",
        api_base_url=API_BASE_URL,
        user_id=user_id,
        profile=profile,
    )


@app.route("/song/<int:song_id>")
async def song_view(song_id: int):
    user_id = _require_user_id()
    if user_id is None:
        return redirect(url_for("login"))
    
    song, profile, liked_songs = await asyncio.gather(
        _safe_get(f"/api/songs/{song_id}", fallback={}),
        _safe_get(f"/api/profile/{user_id}", fallback={}),
        _safe_get(f"/api/liked-songs/{user_id}", fallback=[]),
    )
    song = song or {}
    profile = profile or {}
    liked_songs = liked_songs or []
    song["is_liked"] = any(str(s.get("song_id")) == str(song_id) for s in liked_songs)
    
    is_publisher = False
    if profile.get("user_role") == "artist":
        artist_studio_data = await _safe_get(f"/api/artist-studio/{user_id}", fallback={}) or {}
        artist_id = artist_studio_data.get("artist", {}).get("artist_id")
        if artist_id is not None and artist_id in song.get("artist_ids", []):
            is_publisher = True

    return await render_template(
        "song.html",
        api_base_url=API_BASE_URL,
        user_id=user_id,
        profile=profile,
        song=song,
        is_publisher=is_publisher,
    )


if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", "5000"))
    app.run(host=host, port=port, debug=True)
