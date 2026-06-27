#!/data/data/com.termux/files/usr/bin/python
# ruff: noqa: B019, E501
# pyright: reportUnknownVariableType=false, reportArgumentType=false, reportIndexIssue=information, reportOptionalMemberAccess=information, reportIncompatibleMethodOverride=false, reportCallIssue=false
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import traceback
import urllib.parse
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

import httpx
import yt_dlp
from httpx_retries import Retry, RetryTransport
from slugify import slugify
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.postprocessor.metadataparser import MetadataParserPP

try:
    import pykakasi
except ImportError:
    print("warning: pykakasi not found, Romaji conversion will be disabled.")
    pykakasi = None

try:
    from googletrans import Translator
except ImportError:
    print("warning: googletrans not found, translation features will be disabled.")
    Translator = None

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator, Sequence
    from typing import Any, ClassVar, Literal, NotRequired, TypedDict

IS_TERMUX = (
    "com.termux" in os.environ.get("SHELL", "") or os.environ.get("PREFIX", "") == "/data/data/com.termux/files/usr"
)

###  _____              __ _
### /  __ \            / _(_)
### | /  \/ ___  _ __ | |_ _  __ _
### | |    / _ \| '_ \|  _| |/ _` |
### | \__/\ (_) | | | | | | | (_| |
###  \____/\___/|_| |_|_| |_|\__, |
###                           __/ |
###                          |___/
# Save lyrics as .lrc file
SAVE_LRC = True
# Add Romaji lyrics (requires pykakasi)
ADD_ROMAJI = True
# Add English translation via Google Translate (requires googletrans)
ADD_TRANSLATION = True
# Translation target language code (used for both API and filename suffix)
TRANSLATION_LANG = "en"
# Split lyrics into separate files (original, romaji, translation)
# If False, all lyrics are combined into a single file
SPLIT_LYRICS = True
# Embed lyrics into audio file metadata
EMBED_LYRICS = True
PREFER_SYNCED = True

if IS_TERMUX:

    def notify(  # type: ignore
        title: str,
        content: str,
        *,
        action: str | None = None,
        alert_once: bool = False,
        button1: str | None = None,
        button1_action: str | None = None,
        button2: str | None = None,
        button2_action: str | None = None,
        button3: str | None = None,
        button3_action: str | None = None,
        channel: str | None = None,
        group: str | None = None,
        id: str | None = None,
        on_going: bool = False,
        priority: Literal["high", "low", "max", "min", "default"] = "default",
        sound: bool = False,
        vibrate: bool = False,
        type: Literal["default", "media"] = "default",
        timeout: float | None = None,
    ):
        cmd = ["termux-notification", "--title", title, "--content", content]

        for button, act in [
            (button1, button1_action),
            (button2, button2_action),
            (button3, button3_action),
        ]:
            if act and not button:
                raise ValueError(f"{act} requires the corresponding button to be set")

        options = {
            "--action": action,
            "--button1-text": button1,
            "--button1-action": button1_action,
            "--button2-text": button2,
            "--button2-action": button2_action,
            "--button3-text": button3,
            "--button3-action": button3_action,
            "--channel": channel,
            "--group": group,
            "--id": id,
            "--priority": priority,
            "--type": type,
        }
        flags = {
            "--alert-once": alert_once,
            "--ongoing": on_going,
            "--sound": sound,
            "--vibrate": vibrate,
        }

        cmd.extend([key for key, value in flags.items() if value])
        for key, value in options.items():
            if value:
                cmd.extend([key, str(value)])

        try:
            subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )

        except Exception as e:
            print(f"Error sending notification: {e}")
            return
else:

    def notify(
        title: str,
        content: str,
        **kwargs: Any,
    ):
        print(f"{title}: {content}")


class InnerTubeBase:
    session: httpx.Client | None = None
    _instance = None

    API_KEY = "AIzaSyDkZV5Q2b1e0Qf4Zc0wRjM3vW3rmpZ_mD0"
    INNER_TUBE_BASE = "https://music.youtube.com/youtubei/v1"
    HEADERS: ClassVar = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://music.youtube.com",
        "Referer": "https://music.youtube.com/",
    }
    CLIENT_CONTEXT: ClassVar = {
        "client": {
            "clientName": "WEB_REMIX",
            "clientVersion": "1.20210912.07.00",
        },
    }

    def __new__(cls):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls.session = httpx.Client()
            cls.session.headers.update(cls.HEADERS)
        return cls._instance

    def fetch(self, endpoint: Literal["next", "browse"], payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.INNER_TUBE_BASE}/{endpoint}?key={self.API_KEY}"
        if payload.get("context") is None:
            payload["context"] = self.CLIENT_CONTEXT

        if not self.session:
            raise RuntimeError("session not initialized")
        try:
            response = self.session.post(url, json={**payload, "context": self.CLIENT_CONTEXT})
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            raise ValueError(f"InnerTube API request failed: {e}") from e

    @cache
    def fetch_next(self, video_id: str) -> dict[str, Any]:
        return self.fetch("next", {"videoId": video_id})

    @cache
    def fetch_browse(self, browse_id: str) -> dict[str, Any]:
        return self.fetch("browse", {"browseId": browse_id})


class MetadataPluginBase:
    HEADERS: ClassVar[dict[str, str]] = {}
    COOKIES: ClassVar[dict[str, str]] = {}

    _instance: ClassVar[dict[str, MetadataPluginBase]] = {}
    _session: ClassVar[httpx.Client | None] = None

    @classmethod
    def get_session(cls) -> httpx.Client:
        if MetadataPluginBase._session is None:
            MetadataPluginBase._session = httpx.Client(
                transport=RetryTransport(
                    httpx.HTTPTransport(
                        http2=True,
                        limits=httpx.Limits(max_connections=100, max_keepalive_connections=100, keepalive_expiry=60),
                        verify=False,
                    ),
                    Retry(
                        total=5,
                        backoff_factor=0.5,
                    ),
                )
            )
        return MetadataPluginBase._session

    def __init__(self, info: dict[str, Any], to_screen: Callable[[str], None] | None = None):
        self.video_id = info.get("id") or ""
        self.title = info.get("title") or info.get("track") or ""
        # always pick the first artist to avoid issues
        artists = info.get("artists") or info.get("creators")
        artists_list = (
            [artists]
            if isinstance(artists, str)
            else (artists or [info.get("artist") or info.get("creator") or info.get("uploader", "")])
        )
        self.artist = artists_list[0] if artists_list else ""
        self.album = info.get("album") or info.get("playlist_title") or ""
        self._raw_info = info

        self.inner_tube = InnerTubeBase()
        self.session: httpx.Client = self.get_session()
        self._to_screen: Callable[[str], None] = to_screen or print

    def to_screen(self, message: str):
        self._to_screen(f"{self.__class__.__name__}: {message}")

    def _make_request(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 10,
    ) -> httpx.Response | None:
        req_headers = (self.HEADERS if hasattr(self, "HEADERS") else {}).copy()
        if headers:
            req_headers.update(headers)
        req_cookies = (self.COOKIES if hasattr(self, "COOKIES") else {}).copy()
        if cookies:
            req_cookies.update(cookies)

        try:
            response = self.session.get(
                url,
                headers=req_headers,
                cookies=req_cookies,
                params=params,
                timeout=timeout,
                follow_redirects=True,
            )
            response.raise_for_status()
            return response
        except Exception as e:
            self.to_screen(f"Connection error while making request to {url}: {e}")
            return None

    def get_synced(self) -> str | None:
        return None

    def get_unsynced(self) -> str | None:
        return None

    def enrich_track_data(self) -> dict[str, Any]:
        return {}


class YoutubeMusicPlugin(MetadataPluginBase):
    def get_unsynced(self) -> str | None:
        if not self.video_id:
            return None

        data = self.inner_tube.fetch_next(self.video_id)
        browse_id = self.extract_lyrics_browse_id(data)

        if not browse_id:
            return

        lyrics_data = self.inner_tube.fetch_browse(browse_id)
        lyrics_text = self.extract_lyrics_text(lyrics_data)

        if not lyrics_text:
            return

        return lyrics_text

    def extract_lyrics_text(self, data: dict[str, Any]) -> str | None:
        try:
            lyrics_runs = data["contents"]["sectionListRenderer"]["contents"][0]["musicDescriptionShelfRenderer"][
                "description"
            ]["runs"]
            return "".join([r["text"] for r in lyrics_runs])
        except (KeyError, IndexError):
            return None

    def extract_lyrics_browse_id(self, data: dict[str, Any]) -> str | None:
        try:
            tabs: list[dict[str, Any]] = data["contents"]["singleColumnMusicWatchNextResultsRenderer"][
                "tabbedRenderer"
            ]["watchNextTabbedResultsRenderer"]["tabs"]
            for tab in tabs:
                tab_renderer = tab.get("tabRenderer") or {}
                title = tab_renderer.get("title") or ""
                if title.lower() != "lyrics":
                    continue
                endpoint: dict[str, Any] = tab_renderer.get("endpoint") or {}
                browse_endpoint: dict[str, Any] = endpoint.get("browseEndpoint") or {}
                browse_id: str = browse_endpoint.get("browseId", "")
                if browse_id.startswith("MPLY"):
                    return browse_id
        except (KeyError, IndexError):
            return None


class MusixMatchPlugin(MetadataPluginBase):
    HEADERS: ClassVar = {
        "authority": "apic-desktop.musixmatch.com",
        "cookie": "mxm_bab=AB",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    }

    name: str = "musixmatch"
    token: ClassVar[str | None] = None
    _lyrics_cache: ClassVar[dict[str, dict[str, Any]]] = {}

    def __init__(self, info: dict[str, Any], to_screen: Callable[[str], None] | None = None):
        super().__init__(info, to_screen)

    def _get_token(self, force_refresh: bool = False) -> str | None:
        if not force_refresh and self.token:
            return self.token
        return self._refresh_token()

    def _refresh_token(self) -> str | None:
        try:
            response = self._make_request(
                "https://apic-desktop.musixmatch.com/ws/1.1/token.get",
                params={"app_id": "web-desktop-app-v1.0"},
            )
            if response is None:
                return None
            data = response.json()
            if data["message"]["header"]["status_code"] == 200:
                self.__class__.token = data["message"]["body"]["user_token"]
                return self.token
        except Exception as e:
            print(f"Error fetching MusixMatch token: {e}")
        return None

    def find_lyrics(
        self,
        *,
        album: str = "",
        artist: str = "",
        title: str = "",
        renew: bool = False,
    ) -> dict[str, Any] | None:
        cache_key = f"{artist}:{title}:{album}"
        if cache_key in self._lyrics_cache and not renew:
            return self._lyrics_cache[cache_key]

        token = self._get_token(force_refresh=renew)
        if not token:
            self.to_screen("Could not obtain MusixMatch token.")
            return None

        params = {
            "q_album": album,
            "q_artist": artist,
            "q_track": title,
            "usertoken": token,
            "app_id": "web-desktop-app-v1.0",
            "format": "json",
            "namespace": "lyrics_richsynched",
            "subtitle_format": "mxm",
            "part": "track_lyrics_translation_status,lyrics_crowd,user,subtitle_translated,lyrics_translated,attribution,itunes_commontrack_ids,lyrics_verified_by,labels,track_structure,artist,artist_list,file_upladed_list,uploaded_file_list,artist_image_tagged,artist_image,lyrics_lens,ugc_lyrics_lens,track_performer_tagging",
        }

        try:
            response = self._make_request(
                "https://apic-desktop.musixmatch.com/ws/1.1/macro.subtitles.get",
                params=params,
            )
            if response is None:
                return None
        except Exception as e:
            self.to_screen(f"Error fetching MusixMatch lyrics: {e}")
            return None

        r = response.json()
        status_code = r["message"]["header"]["status_code"]

        if status_code != 200:
            if r["message"]["header"].get("hint") == "renew" or status_code == 401:
                if not renew:
                    self.to_screen("Token expired, renewing...")
                    return self.find_lyrics(album=album, artist=artist, title=title, renew=True)
                else:
                    self.to_screen("Token rejected after renewal.")
                    return None

            self.to_screen(f"API Error: {status_code}")
            return None

        body = r["message"]["body"]["macro_calls"]
        track_status = body["matcher.track.get"]["message"]["header"].get("status_code")

        if track_status != 200:
            if track_status == 404:
                self.to_screen("No lyrics/songs found.")
            elif track_status == 401:
                self.to_screen("Timed out or auth error.")
            else:
                self.to_screen(f"Matcher error: {body['matcher.track.get']['message']['header']}")
            return None

        lyrics_msg = body["track.lyrics.get"]["message"]
        if lyrics_msg.get("header", {}).get("status_code") == 200 and lyrics_msg["body"]["lyrics"]["restricted"]:
            self.to_screen("Restricted lyrics.")
            return None

        self._lyrics_cache[cache_key] = body
        return body

    def get_unsynced(self) -> str | None:
        body = self.find_lyrics(artist=self.artist, title=self.title)
        if body is None:
            return None

        lyrics_body = body["track.lyrics.get"]["message"].get("body")
        if lyrics_body is None:
            return None

        lyrics: str = lyrics_body["lyrics"]["lyrics_body"]
        if lyrics:
            return "\n".join(filter(None, lyrics.split("\n")))

        return None

    def get_synced(self) -> str | None:
        body = self.find_lyrics(artist=self.artist, title=self.title)
        if body is None:
            return None

        subtitle_body = body["track.subtitles.get"]["message"].get("body")
        if subtitle_body is None:
            return None

        subtitle_list = subtitle_body.get("subtitle_list", [])
        if not subtitle_list:
            return None

        subtitle = subtitle_list[0].get("subtitle")
        if subtitle:
            return "\n".join(
                [
                    f"[{line['time']['minutes']:02d}:{line['time']['seconds']:02d}.{line['time']['hundredths']:02d}]{line['text'] or '♪'}"
                    for line in json.loads(subtitle["subtitle_body"])
                ]
            )

        return None

    def enrich_track_data(self) -> dict[str, Any]:
        body = self.find_lyrics(album=self.album, artist=self.artist, title=self.title)
        if not body:
            return {}

        track = body.get("matcher.track.get", {}).get("message", {}).get("body", {}).get("track")
        if not track:
            return {}

        enriched = {}
        if "track_name" in track:
            enriched["title"] = track["track_name"]
        if "artist_name" in track:
            enriched["artist"] = track["artist_name"]
        if "album_name" in track:
            enriched["album"] = track["album_name"]
        if "track_isrc" in track:
            enriched["isrc"] = track["track_isrc"]

        pub_date = track.get("first_release_date")
        if pub_date and len(pub_date) >= 4:
            year_str = pub_date[:4]
            if year_str.isdigit():
                enriched["release_year"] = int(year_str)
            enriched["meta_date"] = year_str

        raw_genre_list = track.get("primary_genres", {}).get("music_genre_list", [])
        if raw_genre_list:
            genre_list: list[str] = []
            for item in raw_genre_list:
                genre_name = item.get("music_genre", {}).get("music_genre_name")
                if genre_name:
                    genre_list.append(genre_name)
            enriched["genre"] = ", ".join(genre_list)

        artist_list = track.get("artist_credits", {}).get("artist_list", [])
        artists = [
            item["artist"]["artist_name"]
            for item in artist_list
            if "artist" in item and "artist_name" in item["artist"]
        ]
        if artists:
            enriched["artists"] = artists
            enriched["creators"] = artists

        return enriched


if TYPE_CHECKING:
    # albumName: "Splash!!"
    # artistName: "Massive New Krew & RoughSketch"
    # artwork: {bgColor: "85b5bb", hasP3: false, height: 3000, textColor1: "0a122a", textColor2: "132837",…}
    # audioLocale: "ja"
    # audioTraits: ["lossless", "lossy-stereo"]
    # composerName: "Massive New Krew"
    # discNumber: 1
    # durationInMillis: 282280
    # genreNames: ["Dance", "Music"]  # TODO: use this later?
    # hasLyrics: true
    # hasTimeSyncedLyrics: true
    # isAppleDigitalMaster: false
    # isMasteredForItunes: false
    # isVocalAttenuationAllowed: true
    # isrc: "JPQ891600122"
    # name: "Extreme Music School (feat. Nanahira)"
    class ShazamSongAttributes(TypedDict):
        albumName: str
        artistName: str
        audioLocale: str
        composerName: str
        discNumber: int
        durationInMillis: int
        hasLyrics: bool
        hasTimeSyncedLyrics: bool
        isrc: str | None
        name: str
        genreNames: list[str]
        releaseDate: str
        trackNumber: int
        url: str

    class ShazamSongData(TypedDict):
        attributes: ShazamSongAttributes
        id: str
        type: str

    class ShazamSong(TypedDict):
        data: list[ShazamSongData]

    class ShazamSongResult(TypedDict):
        songs: ShazamSong

    class ShazamErrorResult(TypedDict):
        id: str
        title: str
        detail: str
        status: str
        code: str
        source: dict[str, str]

    class ShazamSearchResult(TypedDict):
        results: NotRequired[ShazamSongResult]
        errors: NotRequired[list[ShazamErrorResult]]

    ShazamPageCreativeWorkLyrics = TypedDict("ShazamPageCreativeWorkLyrics", {"text": str, "@type": str})

    ShazamPagePerson = TypedDict("ShazamPagePerson", {"name": str, "@type": str})
    ShazamPageMusicComposition = TypedDict(
        "ShazamPageMusicComposition",
        {
            "composer": list[ShazamPagePerson],
            "@type": str,
            "lyrics": ShazamPageCreativeWorkLyrics | None,
        },
    )
    ShazamPageMusicRecordingCompact = TypedDict(
        "ShazamPageMusicRecordingCompact",
        {
            "name": str,
            "url": str,
            "byArtist": str,
            "recordingOf": ShazamPageMusicComposition,
            "@context": str,
            "@type": str,
            "@id": str,
            "lyricist": list[ShazamPagePerson],
            "duration": str | None,
            "description": str | None,
            "genre": str | None,
            "isFamilyFriendly": bool | None,
            "datePublished": str | None,
        },
    )


class ShazamPlugin(MetadataPluginBase):
    name: str = "shazam"
    cache: tuple[str, bool, bool] | None = None
    _search_cache: ClassVar[dict[str, ShazamSongData]] = {}
    _page_cache: ClassVar[dict[str, tuple[str, bool, bool]]] = {}

    HEADERS: ClassVar[dict[str, str]] = {
        "X-Shazam-Platform": "IPHONE",
        "X-Shazam-AppVersion": "14.1.0",
        "accept": "*/*",
        "accept-language": "en-US",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Brave";v="150"',
        "user-agent": "Mozilla/5.0",
    }
    COOKIES: ClassVar[dict[str, str]] = {
        "geoip_country": "GB",
        "_bszm": "2",
    }

    def __init__(self, info: dict[str, Any], to_screen: Callable[[str], None] | None = None):
        super().__init__(info, to_screen)
        self._cached_song_attr: ShazamSongAttributes | None = None

    def handle_error_responses(
        self,
        data: ShazamErrorResult,
        query: str,
        language: str,
        *,
        short_circuit: bool = False,
    ) -> Any:
        if data.get("title", "").lower() == "invalid parameter":
            source = data.get("source", {})
            if not source:
                return

            param = source.get("parameter", "")
            if not param:
                return

            query = query.replace(param, "").strip()
            self.to_screen(f"Retrying without invalid parameter: {param!r}")
            if short_circuit:
                raise ValueError(f"Invalid parameter: {param!r}")

            return self._search_song(query, language, short_circuit=True)

        details = ", ".join(f"{key}={value!r}" for key, value in data.items())
        raise ValueError(f"Shazam API error: {details}")

    def _search_song(self, query: str, language: str = "GB", *, short_circuit: bool = False) -> ShazamSongData | None:
        cache_key = f"{query}:{language}"
        if cache_key in self._search_cache:
            self.to_screen(f"Using cached search result for query: {query!r} (language: {language})")
            return self._search_cache[cache_key]

        resp = self._make_request(
            f"https://www.shazam.com/services/amapi/v1/catalog/{language}/search?types=songs&term={urllib.parse.quote(query)}&limit=3"
        )
        if resp is None:
            raise ValueError("Failed to fetch data")

        data: ShazamSearchResult = resp.json()
        if not data:
            raise ValueError("No data found")

        for error in data.get("errors", []):
            return self.handle_error_responses(
                error,
                query,
                language,
                short_circuit=short_circuit,
            )

        def _match_title(t1: str, t2: str, threshold: float = 0.7) -> bool:
            sm = SequenceMatcher(
                lambda x: x in ("-", "_"),
                t1.lower(),
                t2.lower(),
            )
            ratio = round(sm.ratio(), 2)
            return ratio >= threshold

        def _fuzzy_match(t1: str, t2: str, threshold: float = 0.7) -> bool:
            if _match_title(t1, t2, threshold) or _match_title(t2, t1, threshold):
                return True
            return _match_title(self._clean_title(t1), self._clean_title(t2), threshold) or _match_title(
                self._clean_title(t2), self._clean_title(t1), threshold
            )

        def _get_artists_set(artist_str: str) -> set[str]:
            normalized = artist_str.lower()
            for delim in (" & ", " feat. ", " feat ", " ft. ", " ft ", " x ", " + ", " with ", " and "):
                normalized = normalized.replace(delim, ", ")
            return {a.strip() for a in normalized.split(",") if a.strip()}

        def _match_artists(a1: str, a2: str) -> bool:
            if _fuzzy_match(a1, a2, 0.7):
                return True
            s1 = _get_artists_set(a1)
            s2 = _get_artists_set(a2)
            for x1 in s1:
                for x2 in s2:
                    if x1 == x2:
                        return True
                    if len(x1) >= 2 and len(x2) >= 2:
                        pattern1 = r"\b" + re.escape(x1) + r"\b"
                        pattern2 = r"\b" + re.escape(x2) + r"\b"
                        if re.search(pattern1, x2) or re.search(pattern2, x1):
                            return True
                    sm = SequenceMatcher(None, x1, x2)
                    if sm.ratio() >= 0.8:
                        return True
            return False

        for song in data.get("results", {}).get("songs", {}).get("data", []):
            if not _match_artists(self.artist, song["attributes"]["artistName"]):
                continue

            if _fuzzy_match(self.title, song["attributes"]["name"]):
                self._search_cache[cache_key] = song
                return cast("ShazamSongData", song)

        tags = self._raw_info.get("tags", [])
        if not tags:
            raise ValueError("No matching song found")

        for tag in tags:
            for song in data.get("results", {}).get("songs", {}).get("data", []):
                if not _match_artists(self.artist, song["attributes"]["artistName"]):
                    continue

                if _fuzzy_match(tag, song["attributes"]["name"]):
                    self._search_cache[cache_key] = song
                    return cast("ShazamSongData", song)

        raise ValueError("No matching song found")

    def _get_real_page(self, track_id: str, track_name: str) -> str | None:
        song_page = self._make_request(
            f"https://www.shazam.com/song/{track_id}/{urllib.parse.quote(track_name.replace(' ', '-'))}"
        )
        if song_page is None:
            return None

        regex = r"<link\s+rel=\"canonical\"\s+href=\"([^\"]+)"
        match = re.search(regex, song_page.text, re.NOFLAG)
        if match:
            return match.group(1)
        return None

    def _clean_title(self, title: str) -> str:
        title = re.sub(r"\s*([(\[])(?:feat|ft)\.?\s+[^)\]]+([)\]])", "", title, flags=re.IGNORECASE)
        return title.strip()

    def _deep_search_all(self, data: dict[str, Any] | list[Any] | Any, target_key: str) -> Iterator[Any]:
        if isinstance(data, dict):
            if target_key in data:
                yield data[target_key]

            for value in data.values():
                if TYPE_CHECKING:
                    value: Any
                yield from self._deep_search_all(value, target_key)

        elif isinstance(data, list):
            for item in data:
                yield from self._deep_search_all(item, target_key)

    def get_unsynced(self) -> str | None:
        if not self._has_lyrics():
            return None

        pattern = r'<script type="application/ld\+json">(.*?)</script>'
        match = re.search(pattern, self._page_content(), re.DOTALL)
        if not match:
            self.to_screen("Failed to find lyrics data in the song page.")
            return None

        json_data = match.group(1).strip()
        data: ShazamPageMusicRecordingCompact = json.loads(json_data)
        lyrics_data = data["recordingOf"]["lyrics"]
        if not lyrics_data:
            self.to_screen("No lyrics found for this track.")
            return None

        return lyrics_data["text"]

    def _get_synced_lyrics(self) -> Iterator[str]:
        pattern = r"self\.__next_f\.push\(\[\s*1,\s*\"..?:(.*?)\"\]\)"
        match = re.finditer(pattern, self._page_content(), re.DOTALL)
        if not match:
            self.to_screen("Failed to find synced lyrics data in the song page.")
            return

        lyrics_block_js_string = None
        for m in match:
            if not m:
                continue
            content = m.group(1)
            if "startTimeInSeconds" in content or "endTimeInSeconds" in content:
                lyrics_block_js_string = content
                break

        if not lyrics_block_js_string:
            self.to_screen("No synced lyrics found for this track.")
            return

        # lyrics_block_js_string = (
        #     lyrics_block_js_string.encode().decode("unicode_escape").encode("latin-1").decode("utf-8")
        # )
        try:
            lyrics_block_js_string = json.loads(f'"{lyrics_block_js_string}"')
            lyrics_data = json.loads(lyrics_block_js_string)
            if not lyrics_data:
                self.to_screen("No synced lyrics data could be parsed.")
                return
        except (json.JSONDecodeError, ValueError, TypeError):
            self.to_screen("Failed to parse synced lyrics JSON.")
            return

        lyric_lines_gen = self._deep_search_all(lyrics_data, "lyricLines")
        if TYPE_CHECKING:
            lyric_lines_gen: Iterator[list[dict[str, Any]]]

        for lyric_lines in lyric_lines_gen:
            if len(lyric_lines) > 0:
                for line in lyric_lines:
                    time_raw: str = line.get("startTimeInSeconds", "0")
                    try:
                        time_float = float(time_raw)
                        total_hundredths = round(time_float * 100)
                        minutes = total_hundredths // 6000
                        seconds = (total_hundredths // 100) % 60
                        hundredths = total_hundredths % 100
                        start_time = f"{minutes:02d}:{seconds:02d}.{hundredths:02d}"
                    except ValueError:
                        parts = time_raw.split(":")
                        if len(parts) == 3:
                            hours = int(parts[0])
                            minutes = int(parts[1])
                            seconds = float(parts[2])
                            if hours > 0:
                                minutes += hours * 60
                            start_time = f"{minutes:02d}:{seconds:05.2f}"
                        elif len(parts) == 2:
                            minutes = int(parts[0])
                            seconds = float(parts[1])
                            start_time = f"{minutes:02d}:{seconds:05.2f}"
                        else:
                            start_time = "00:00.000"

                    text = line.get("content", "♪")
                    yield f"[{start_time}] {text}"

    def get_synced(self) -> str | None:
        if not self._has_synced_lyrics():
            return None
        lines = list(self._get_synced_lyrics())
        if not lines:
            self.to_screen("No synced lyrics lines could be extracted.")
            return None
        return "\n".join(lines)

    def raw_data(self) -> tuple[str, bool, bool]:
        if not self.cache:
            self.cache = self._get_page_data()

        if self.cache is None:
            return "", False, False
        return self.cache

    def _page_content(self) -> str:
        return self.raw_data()[0]

    def _has_lyrics(self) -> bool:
        return self.raw_data()[1]

    def _has_synced_lyrics(self) -> bool:
        return self.raw_data()[2]

    def _get_page_data(self) -> tuple[str, bool, bool] | None:
        matched_song = None
        query = f"{self.artist} {self._clean_title(self.title)}" if self.artist else self._clean_title(self.title)
        for language in ["GB", "JP"]:
            try:
                matched_song = self._search_song(query, language)
                if matched_song:
                    break
            except ValueError as e:
                self.to_screen(repr(e))
                continue

        if not matched_song:
            self.to_screen("No matching track found on Shazam.")
            return None

        self._cached_song_attr = matched_song["attributes"]
        track_id = matched_song["id"]

        if track_id in self._page_cache:
            return self._page_cache[track_id]

        track_name = matched_song["attributes"]["name"]
        has_lyrics = matched_song["attributes"]["hasLyrics"]
        has_synced_lyrics = matched_song["attributes"]["hasTimeSyncedLyrics"]

        song_url = self._get_real_page(track_id, slugify(track_name))
        if not song_url:
            self.to_screen("Failed to retrieve the song page.")
            return None

        song_page = self._make_request(song_url)
        if song_page is None:
            self.to_screen("Failed to retrieve the song page content.")
            return None

        res = (song_page.text, has_lyrics, has_synced_lyrics)
        self._page_cache[track_id] = res
        return res

    def enrich_track_data(self) -> dict[str, Any]:
        if not self._cached_song_attr:
            self.raw_data()

        if not self._cached_song_attr:
            return {}

        song_attr = self._cached_song_attr
        enriched = {}
        # maybe use this later for more accurate metadata, but for now, we rely on the original info
        if "name" in song_attr:
            enriched["track"] = song_attr["name"]
        if "artistName" in song_attr:
            enriched["artist"] = song_attr["artistName"]
            artists = [a.strip() for a in re.split(r"[&,]|feat\.?", song_attr["artistName"]) if a.strip()]
            if artists:
                enriched["artists"] = artists
                enriched["creators"] = artists
        if "albumName" in song_attr:
            enriched["album"] = song_attr["albumName"]
        if song_attr.get("genreNames"):
            enriched["genre"] = ", ".join(song_attr["genreNames"])
        if "composerName" in song_attr:
            enriched["composer"] = song_attr["composerName"]
        if "isrc" in song_attr:
            enriched["isrc"] = song_attr["isrc"]

        pub_date = song_attr.get("releaseDate") or song_attr.get("datePublished")
        if pub_date and len(pub_date) >= 4:
            year_str = pub_date[:4]
            if year_str.isdigit():
                enriched["release_year"] = int(year_str)
            enriched["meta_date"] = year_str

        return enriched


if TYPE_CHECKING:

    class LrcLibResponse(TypedDict):
        id: str
        name: str
        trackName: str
        artistName: str
        albumName: str
        duration: float
        instrumental: bool
        plainLyrics: str
        syncedLyrics: str


class LrcLibPlugin(MetadataPluginBase):
    BASE_URL = "https://lrclib.net/api"
    HEADERS: ClassVar = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    def __init__(self, info: dict[str, Any], to_screen: Callable[[str], None] | None = None):
        super().__init__(info, to_screen=to_screen)
        self.album = info.get("album") or info.get("playlist_title") or ""
        self._lyrics_data: LrcLibResponse | None = None

    def find_lyrics(
        self,
        *,
        q: str = "",
        track_name: str = "",
        artist_name: str = "",
        album_name: str = "",
    ) -> LrcLibResponse | None:
        params = {
            "q": q,
            "track_name": track_name,
            "artist_name": artist_name,
            "album_name": album_name,
        }
        try:
            response = self._make_request(self.BASE_URL + "/search", params=params)
            if response is None:
                return None
            for item in response.json():
                if item.get("track_name") == track_name and item.get("artist_name") == artist_name:
                    return item
            return None
        except Exception as e:
            self.to_screen(repr(e))
            return None

    @property
    def lyrics_data(self) -> LrcLibResponse | None:
        if self._lyrics_data is None:
            self._lyrics_data = self.find_lyrics(
                track_name=self.title or "",
                artist_name=self.artist or "",
                album_name=self.album,
            )

        return self._lyrics_data

    def get_unsynced(self) -> str | None:
        if self.lyrics_data:
            return self.lyrics_data.get("plainLyrics", None)

    def get_synced(self) -> str | None:
        if self.lyrics_data:
            return self.lyrics_data.get("syncedLyrics", None)


@cache
def fetch_album_info(browse_id: str | None) -> dict[str, Any]:
    if not browse_id:
        raise ValueError("Invalid browse ID")

    options = {
        "extract_flat": "in_playlist",
        "format": "best",
        "quiet": True,
        "skip_download": True,
        "source_address": None,
    }

    with yt_dlp.YoutubeDL(options) as _ydl:
        album_info = _ydl.extract_info(f"https://music.youtube.com/browse/{browse_id}", download=False)
        if not album_info or not isinstance(album_info, dict) or "entries" not in album_info:
            raise ValueError("Failed to get data from album")

        return album_info


@cache
def find_album_browse_id(video_id: str) -> str | None:
    data = InnerTubeBase().fetch_next(video_id)

    # Recursively find the first MPREb ID (albums/singles)
    def find(obj: Any) -> str | None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "browseEndpoint" and isinstance(v, dict):
                    browse_id = v.get("browseId")
                    if isinstance(browse_id, str) and browse_id.startswith("MPREb"):
                        return browse_id
                result = find(v)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = find(item)
                if result:
                    return result
        return None

    return find(data)


@cache
def find_album_info(video_id: str) -> dict[str, Any]:
    """Find album info from the music URL or video ID."""

    album_browse_id = find_album_browse_id(video_id)
    if not album_browse_id:
        raise ValueError("Failed to fetch album URL")

    return fetch_album_info(album_browse_id)


def get_track_num(video_id: str) -> str:
    """Find track number from related album info."""
    album_info = find_album_info(video_id)
    for idx, entry in enumerate(album_info.get("entries", []), start=1):
        if entry.get("id") == video_id:
            return f"{idx:02d}"

    raise ValueError("Video ID not found from the album.")


@cache
def _parse_release_year(album_browse_id: str) -> str:
    album_details = InnerTubeBase().fetch_browse(album_browse_id)
    try:
        year = album_details["contents"]["twoColumnBrowseResultsRenderer"]["tabs"][0]["tabRenderer"]["content"][
            "sectionListRenderer"
        ]["contents"][0]["musicResponsiveHeaderRenderer"]["subtitle"]["runs"][-1]["text"]
        return year
    except (KeyError, IndexError):
        raise ValueError("Release year not found from the album.") from None


def get_release_year_from_album(video_id: str) -> str:
    album_browse_id = find_album_browse_id(video_id)
    return _parse_release_year(album_browse_id)


@cache
def _parse_album_artist(album_browse_id: str) -> str:
    album_details = InnerTubeBase().fetch_browse(album_browse_id)
    try:
        # album_artist = album_details["contents"]["twoColumnBrowseResultsRenderer"]["secondaryContents"][
        #     "sectionListRenderer"
        # ]["contents"][-1]["musicCarouselShelfRenderer"]["contents"][1]["musicTwoRowItemRenderer"]["subtitle"]["runs"][
        #     -1
        # ]["text"]
        album_artist = album_details["contents"]["twoColumnBrowseResultsRenderer"]["tabs"][0]["tabRenderer"]["content"][
            "sectionListRenderer"
        ]["contents"][0]["musicResponsiveHeaderRenderer"]["straplineTextOne"]["runs"][0]["text"]
        return album_artist
    except (KeyError, IndexError):
        raise ValueError("Album artist not found from the album.") from None


def get_album_artist(video_id: str) -> str:
    album_browse_id = find_album_browse_id(video_id)
    return _parse_album_artist(album_browse_id)


@cache
def is_various_artist(album_browse_id: str) -> bool:
    album_info = fetch_album_info(album_browse_id)
    if not album_info:
        raise ValueError("Failed to get data from album")

    entries = album_info.get("entries", [])
    if not entries:
        return False
    first_channel = entries[0].get("channel_id")
    return any(entry.get("channel_id") != first_channel for entry in entries[1:])


class ExtraMetadataPP(PostProcessor):
    def run(self, information: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
        self.to_screen("Checking metadata...")
        video_id = information["id"]

        chnl = information.get("channel") or information.get("uploader") or ""
        if chnl.endswith(" - Topic"):
            raw = information.get("artists") or information.get("artist", "").split(", ")
            artists: list[str] = list(dict.fromkeys(filter(None, raw)))
            if not artists:
                artists = [chnl.replace(" - Topic", "")]
            information.update(
                {
                    "artists": artists,
                    "creators": artists,
                    "artist": ", ".join(artists),
                    "creator": ", ".join(artists),
                }
            )

        if not (information.get("playlist_id") or "").startswith("OLAK5uy_"):
            self.to_screen("Not an album context, getting metadata for album manually")
            try:
                information["track_number"] = get_track_num(video_id)
            except ValueError:
                self.to_screen("Hmm, doesn't look like an album. Skipping...")

        try:
            information["meta_album_artist"] = get_album_artist(video_id)
        except ValueError:
            self.to_screen("No album artist found from innertube data, trying legacy methods...")
            try:
                browse_id = find_album_browse_id(video_id)
                if browse_id and is_various_artist(browse_id):
                    self.to_screen("Album is a Various Artists compilation")
                    information["meta_album_artist"] = "Various Artists"
            except ValueError:
                self.to_screen("Hmm, doesn't look like an album. Skipping...")

        if not information.get("meta_album_artist") or information.get("meta_album_artist") == "NA":
            self.to_screen("No album artist info found, using track artist instead")
            information["meta_album_artist"] = information.get("artist") or "Unknown Artist"

        if not information.get("meta_date"):
            self.to_screen("No release year found, getting info from innertube album data...")
            try:
                information["meta_date"] = get_release_year_from_album(video_id)
            except ValueError:
                self.to_screen("Release year not found from the album. Too bad...")

        plugins: Sequence[MetadataPluginBase] = [
            MusixMatchPlugin(information, to_screen=self.to_screen),
            ShazamPlugin(information, to_screen=self.to_screen),
        ]
        for plugin in plugins:
            try:
                enriched = plugin.enrich_track_data()
                if enriched:
                    self.to_screen(f"Enriched metadata from {plugin.__class__.__name__}: {list(enriched.keys())}")
                    # information.update(enriched)
                    if "genre" in enriched:
                        information["genre"] = enriched["genre"]
                        information["meta_genre"] = enriched["genre"]
                        break  # extend this to other metadata if needed, but for now, we only care about genre
            except Exception as e:
                self.to_screen(f"Failed to enrich track metadata from {plugin.__class__.__name__}: {e}")

        return [], information


class EmbedLyricsMetadataPP(PostProcessor):
    _kks = pykakasi.kakasi() if ADD_ROMAJI and pykakasi else None
    _timestamp_pattern = re.compile(r"^(\[[\d:.]+\])(.*)$")
    _jp_pattern = re.compile(r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]")

    @staticmethod
    def run_coroutine_sync[T](coroutine: Coroutine[Any, Any, T], timeout: float = 30) -> T:
        def run_in_new_loop():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                return new_loop.run_until_complete(coroutine)
            finally:
                new_loop.close()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)

        if threading.current_thread() is threading.main_thread():
            if not loop.is_running():
                return loop.run_until_complete(coroutine)
            else:
                with ThreadPoolExecutor() as pool:
                    future = pool.submit(run_in_new_loop)
                    return future.result(timeout=timeout)
        else:
            return asyncio.run_coroutine_threadsafe(coroutine, loop).result()

    @property
    def kks(self):
        if not pykakasi:
            raise ImportError("pykakasi is required for romaji conversion")
        if not self._kks:
            if ADD_ROMAJI:
                raise ValueError("Kakasi instance not initialized")
            else:
                raise ValueError("Kakasi instance not available because ADD_ROMAJI is False. This should never happen.")
        return self._kks

    def run(self, information: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
        if not SAVE_LRC:
            return [], information

        is_synced, lyrics = self.get_lyrics(information)
        if lyrics:
            if ADD_ROMAJI or ADD_TRANSLATION:
                result = self._process_lyrics(lyrics, is_synced)
                if SPLIT_LYRICS:
                    information["_lyrics_original"] = result["original"]
                    information["_lyrics_romaji"] = result["romaji"]
                    information["_lyrics_translation"] = result["translation"]
                    lyrics = result["original"]
                else:
                    lyrics = result["combined"]

            if is_synced:
                information["_is_synced_lyrics"] = True
            information["meta_lyrics" if EMBED_LYRICS else "_lyrics"] = lyrics

        return [], information

    def _to_romaji(self, text: str) -> str:
        out = ""
        for item in self.kks.convert(text):
            word = item["hepburn"]
            if not word:
                continue
            if not out or out[-1].isspace() or word[0].isspace():
                out += word
            else:
                out += " " + word
        return out

    if TYPE_CHECKING:

        class TranslationResult(TypedDict):
            combined: str
            original: str
            romaji: str | None
            translation: str | None

    def _process_lyrics(self, lyrics: str, is_synced: bool) -> TranslationResult:
        lines = lyrics.splitlines()

        japanese_entries: list[tuple[int, str | None, str]] = []
        cached_matches: dict[int, tuple[str, str]] = {}

        for i, line in enumerate(lines):
            if is_synced:
                m = self._timestamp_pattern.match(line)
                if m:
                    timestamp, content = m.groups()
                    cached_matches[i] = (timestamp, content)
                    if content and self._jp_pattern.search(content):
                        japanese_entries.append((i, timestamp, content))
            elif self._jp_pattern.search(line):
                japanese_entries.append((i, None, line))

        if not japanese_entries:
            return {
                "combined": lyrics,
                "original": lyrics,
                "romaji": None,
                "translation": None,
            }

        translations: dict[int, str] = {}
        if ADD_TRANSLATION and Translator is not None and japanese_entries:
            try:
                self.to_screen(f"translating {len(japanese_entries)} line(s) to {TRANSLATION_LANG}...")
                texts = [e[2] for e in japanese_entries]
                translator = Translator()
                results = self.run_coroutine_sync(translator.translate(texts, src="ja", dest=TRANSLATION_LANG))
                if not isinstance(results, list):
                    results = [results]
                translations = {e[0]: r.text for e, r in zip(japanese_entries, results, strict=True) if r.text}
            except Exception as exc:
                self.to_screen(f"failed to translate: {exc}")

        jp_set = {e[0] for e in japanese_entries}

        combined_lines: list[str] = []
        romaji_lines: list[str] = []
        translation_lines: list[str] = []

        for i, line in enumerate(lines):
            combined_lines.append(line)
            if i in jp_set:
                if is_synced:
                    timestamp, content = cached_matches[i]
                    if ADD_ROMAJI:
                        romaji_text = self._to_romaji(content)
                        combined_lines.append(f"{timestamp} {romaji_text}")
                        romaji_lines.append(f"{timestamp} {romaji_text}")
                    if ADD_TRANSLATION:
                        if i in translations:
                            combined_lines.append(f"{timestamp} {translations[i]}")
                            translation_lines.append(f"{timestamp} {translations[i]}")
                        else:
                            translation_lines.append(f"{timestamp}")
                else:
                    if ADD_ROMAJI:
                        romaji_text = self._to_romaji(line)
                        combined_lines.append(romaji_text)
                        romaji_lines.append(romaji_text)
                    if ADD_TRANSLATION:
                        if i in translations:
                            combined_lines.append(translations[i])
                            translation_lines.append(translations[i])
                        else:
                            translation_lines.append("")
            else:
                # non-japanese lines go into romaji/translation as-is
                romaji_lines.append(line)
                translation_lines.append(line)

            combined_lines.append("")

        return {
            "combined": "\n".join(combined_lines),
            "original": lyrics,
            "romaji": "\n".join(romaji_lines) if ADD_ROMAJI and romaji_lines else None,
            "translation": "\n".join(translation_lines) if ADD_TRANSLATION and translations else None,
        }

    def get_lyrics(self, information: dict[str, Any]) -> tuple[bool, str | None]:
        self.to_screen("Fetching lyrics...")

        plugin_classes: list[type[MetadataPluginBase]] = [
            ShazamPlugin,
            LrcLibPlugin,
            MusixMatchPlugin,
            YoutubeMusicPlugin,
        ]

        lyrics: str | None = None
        is_synced: bool = False

        for plugin_cls in plugin_classes:
            try:
                self.to_screen(f"Checking synced lyrics with {plugin_cls.__name__}...")
                plugin = plugin_cls(information, to_screen=self.to_screen)
                lyrics = plugin.get_synced()
                if lyrics:
                    self.to_screen("Found synced lyrics.")
                    is_synced = True
                    break
            except Exception as e:
                self.to_screen(f"Error while checking synced lyrics with {plugin_cls.__name__}: {e}")
                self.to_screen(traceback.print_exc())

        if PREFER_SYNCED and not lyrics:
            for plugin_cls in plugin_classes:
                try:
                    self.to_screen(f"Checking unsynced lyrics with {plugin_cls.__name__}...")
                    plugin = plugin_cls(information, to_screen=self.to_screen)
                    lyrics = plugin.get_unsynced()
                    if lyrics:
                        self.to_screen("Found unsynced lyrics.")
                        break
                except Exception as e:
                    self.to_screen(f"Error while checking unsynced lyrics with {plugin_cls.__name__}: {e}")
                    self.to_screen(traceback.print_exc())

        return is_synced, lyrics


class SaveLyricsToFilePP(PostProcessor):
    def run(self, information: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
        if not SAVE_LRC:
            return [], information

        is_synced = information.get("_is_synced_lyrics", False)
        suffix = ".lrc" if is_synced else ".txt"
        base = Path(f"{information['filepath']}")

        if SPLIT_LYRICS:
            self._save_split(base, suffix, information)
        else:
            lyrics = information.get("meta_lyrics") or information.get("_lyrics")
            if lyrics:
                _filename = base.with_suffix(suffix)
                _filename.write_text(lyrics, encoding="utf-8")
                self.to_screen(f"Saved lyrics to {_filename}")

        return [], information

    def _save_split(
        self,
        base: Path,
        suffix: str,
        information: dict[str, Any],
    ) -> None:
        original = information.get("_lyrics_original") or information.get("meta_lyrics") or information.get("_lyrics")
        if original:
            path = base.with_suffix(suffix)
            path.write_text(original, encoding="utf-8")
            self.to_screen(f"Saved original lyrics to {path}")

        romaji = information.get("_lyrics_romaji")
        if romaji:
            path = base.with_suffix(f".romaji{suffix}")
            path.write_text(romaji, encoding="utf-8")
            self.to_screen(f"Saved romaji lyrics to {path}")

        translation = information.get("_lyrics_translation")
        if translation:
            path = base.with_suffix(f".{TRANSLATION_LANG}{suffix}")
            path.write_text(translation, encoding="utf-8")
            self.to_screen(f"Saved translated lyrics to {path}")


ytdl_opts = {
    "extract_flat": False,
    "format": "bestaudio[ext=webm]/bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio/best/b",
    "fragment_retries": 10,
    "ignoreerrors": "only_download",
    "outtmpl": {
        "default": "Album/%(album|Unknown Album)s/%(track_number,playlist_index)02d %(title)s.%(ext)s",
        "pl_thumbnail": "",
    },
    "allowed_extractors": [r"^[yY].*[tT].*e?$"],
    "postprocessors": [
        {
            "actions": [
                (
                    MetadataParserPP.interpretter,
                    "",
                    "(?P<meta_synopsis>)",
                ),
                (
                    MetadataParserPP.interpretter,
                    "",
                    "(?P<meta_date>)",
                ),
                (
                    MetadataParserPP.replacer,
                    "meta_artist",
                    " - Topic$",
                    "",
                ),
                (
                    MetadataParserPP.interpretter,
                    "artist",
                    "(?P<meta_album_artist>.*)",
                ),
                (
                    MetadataParserPP.replacer,
                    "meta_album_artist",
                    "[,/&].+",
                    "",
                ),
                (
                    MetadataParserPP.interpretter,
                    "%(track_number,playlist_index|01)s",
                    "%(track_number)s",
                ),
                (
                    MetadataParserPP.interpretter,
                    "%(album,playlist_title|Unknown Album)s",
                    "%(album)s",
                ),
                (
                    MetadataParserPP.replacer,
                    "album",
                    "^Album - ",
                    "",
                ),
                (
                    MetadataParserPP.interpretter,
                    "%(genre|Unknown Genre)s",
                    "%(genre)s",
                ),
                (
                    MetadataParserPP.interpretter,
                    "description",
                    "(?P<meta_date>(?<=Released on: )\\d{4})",
                ),
                (
                    MetadataParserPP.interpretter,
                    "",
                    "(?P<description>)",
                ),
            ],
            "key": "MetadataParser",
            "when": "pre_process",
        },
        {
            "format": "png",
            "key": "FFmpegThumbnailsConvertor",
            "when": "before_dl",
        },
        {
            "key": "FFmpegExtractAudio",
            "nopostoverwrites": False,
            "preferredcodec": "best",
            "preferredquality": "5",
        },
        {
            "add_chapters": True,
            "add_infojson": "if_exists",
            "add_metadata": True,
            "key": "FFmpegMetadata",
        },
        {"already_have_thumbnail": False, "key": "EmbedThumbnail"},
        {"key": "FFmpegConcat", "only_multi_video": True, "when": "playlist"},
        # {
        #     "exec_cmd": ["echo after_move: {}"],
        #     "key": "Exec",
        #     "when": "after_move",
        # },
        # {
        #     "exec_cmd": ["echo playlist: {}"],
        #     "key": "Exec",
        #     "when": "playlist",
        # },
    ],
    "extractor_args": {
        "youtube": {
            "lang": ["en"],
            "player_client": ["mweb"],
            "formats": "missing_pot",
        },
        "youtubepot-bgutilhttp": {"base_url": ["https://bgutil-ytdlp-pot-vercal.vercel.app"]},
    },
    "retries": 10,
    "updatetime": False,
    "verbose": True,
    "writethumbnail": True,
}

if IS_TERMUX:
    termux_opts = {
        "cachedir": "$HOME/.config/yt-dlp/",
        "cookiefile": "$HOME/.config/yt-dlp/youtube.com_cookies.txt",
        "allowed_extractors": ["^([yY].*?)([tT]).*e?$"],
        "outtmpl": {
            "default": "/sdcard/Music/%(album|Unknown Album)s/%(track_number,playlist_index)02d %(title)s.%(ext)s"
        },
        "overwrites": False,
    }
    ytdl_opts.update(termux_opts)
    # ytdl_opts["extractor_args"]["youtube"]["getpot_bgutil_script"] = (
    #     "$HOME/projects/bgutil-ytdlp-pot-provider/server/build/generate_once.js",
    # )
    ytdl_opts["postprocessors"].extend(  # type: ignore
        [
            {
                "exec_cmd": ["termux-media-scan {}"],
                "key": "Exec",
                "when": "after_move",
            },
            {
                "exec_cmd": [
                    "ffmpeg -i %(thumbnails.-1.filepath)q -vf "
                    "crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\" "
                    "%(thumbnails.-1.filepath)q.png > /dev/null "
                    "2>&1",
                    "mv %(thumbnails.-1.filepath)q.png %(thumbnails.-1.filepath)q",
                ],
                "key": "Exec",
                "when": "before_dl",
            },
        ]
    )
elif os.name == "nt":
    win_opts = {
        "js_runtimes": {"node": {}},
        "remote_components": {"ejs:github"},
        "cookiesfrombrowser": ("firefox",),
        "extractor_args": {
            "youtube": {"player_js_variant": ("tv",)},
        },
    }
    ytdl_opts.update(win_opts)


def download(url: str, extra_options: dict[str, Any] | None = None):
    options = ytdl_opts.copy()
    if extra_options:
        options.update(extra_options)

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.add_post_processor(ExtraMetadataPP(), when="pre_process")
        ydl.add_post_processor(EmbedLyricsMetadataPP(ydl), when="pre_process")
        ydl.add_post_processor(SaveLyricsToFilePP(ydl), when="after_move")
        ydl.download([url])


def main(url: str | None = None):
    if not url:
        if len(sys.argv) < 2:
            print("Usage: python ytmusic-downloader.py <url>")
            return
        url = sys.argv[1]

    try:
        download(url)
    except Exception as e:
        notify(
            title=e.__class__.__name__,
            content=str(e),
            id="download_error",
            # action="termux-open-url",
            button1="OK",
            button1_action="termux-notification-remove --id download_error",
        )
        raise


if __name__ == "__main__":
    main()
    # sys.exit(main("https://music.youtube.com/watch?v=iyilehKHCKg"))
