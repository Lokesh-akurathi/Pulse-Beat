from concurrent.futures import ThreadPoolExecutor
from backend.db import repositories as repo


def get_home_payload(user_id: int):
    with ThreadPoolExecutor(max_workers=4) as executor:
        f_profile = executor.submit(repo.get_user_profile, user_id)
        f_trending = executor.submit(repo.get_trending_songs)
        f_artists = executor.submit(repo.get_all_artists, 12)
        f_recent = executor.submit(repo.get_recent_songs, user_id)

    profile = f_profile.result()
    if not profile:
        raise ValueError("User not found")

    return {
        "profile": profile,
        "trending_songs": f_trending.result() or [],
        "recommendations": [],
        "playlists": [],
        "song_sales": [],
        "liked_songs": [],
        "followed_artists": [],
        "all_artists": f_artists.result() or [],
        "genres": [],
        "recent_songs": f_recent.result() or [],
    }


def search_payload(user_id: int, term: str):
    return {
        "songs": repo.search_songs(term),
        "artists": repo.search_artists(term),
        "playlists": repo.search_playlists(term, user_id),
    }
