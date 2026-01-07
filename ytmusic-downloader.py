#!/data/data/com.termux/files/usr/bin/python
# ruff: noqa: B019, S105, E501
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import traceback
from collections import OrderedDict
from collections.abc import Callable, Generator, Iterator
from difflib import SequenceMatcher
from functools import cache, lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypedDict

import requests
import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.postprocessor.ffmpeg import FFmpegMetadataPP
from yt_dlp.postprocessor.metadataparser import MetadataParserPP

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator
    from typing import Any

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
# Embed lyrics into audio file metadata
EMBED_LYRICS = False
PREFER_SYNCED = True


def notify(
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
    cmd.extend([f"{key} {value}" for key, value in options.items() if value])

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


class InnerTubeBase:
    if TYPE_CHECKING:
        session: requests.Session  # type: ignore

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
            cls._instance.session = requests.Session()
            cls._instance.session.headers.update(cls.HEADERS)
        return cls._instance

    def fetch(self, endpoint: Literal["next", "browse"], payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.INNER_TUBE_BASE}/{endpoint}?key={self.API_KEY}"
        if payload.get("context") is None:
            payload["context"] = self.CLIENT_CONTEXT

        response = self.session.post(url, json={**payload, "context": self.CLIENT_CONTEXT})
        response.raise_for_status()
        return response.json()

    @cache
    def fetch_next(self, video_id: str) -> dict[str, Any]:
        return self.fetch("next", {"videoId": video_id})

    @cache
    def fetch_browse(self, browse_id: str) -> dict[str, Any]:
        return self.fetch("browse", {"browseId": browse_id})


class LyricsPluginBase:
    if TYPE_CHECKING:
        video_id: str
        title: str | None
        artist: str | None

    def __init__(self, info: dict[str, Any], to_screen: Callable[[str], None] | None = None):
        self.video_id = info.get("id") or ""
        self.title = info.get("title")
        self.artist = (info.get("artists") or info.get("creators") or [info.get("uploader", "")])[
            0
        ]  # always pick the first artist to avoid issues

        self.inner_tube = InnerTubeBase()
        self._to_screen: Callable[[str], None] = to_screen or print

    def to_screen(self, message: str):
        self._to_screen(f"{self.__class__.__name__}: {message}")

    def get_synced(self) -> str | None:
        raise NotImplementedError("Subclasses must implement this method")

    def get_unsynced(self) -> str | None:
        raise NotImplementedError("Subclasses must implement this method")


class YoutubeMusicLyricsPlugin(LyricsPluginBase):
    def get_synced(self) -> None:
        return

    def get_unsynced(self):
        if not self.video_id:
            return

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
        tabs = (
            data.get("contents", {})
            .get("singleColumnMusicWatchNextResultsRenderer", {})
            .get("tabbedRenderer", {})
            .get("watchNextTabbedResultsRenderer", {})
            .get("tabs", [])
        )
        for tab in tabs:
            endpoint = tab.get("tabRenderer", {}).get("endpoint", {}).get("browseEndpoint", {})
            browse_id = endpoint.get("browseId", "")
            if browse_id.startswith("MPLY"):
                return browse_id
        return None


class MusixMatchLyricsPlugin(LyricsPluginBase):
    TOKEN = "2203269256ff7abcb649269df00e14c833dbf4ddfb5b36a1aae8b0"
    BASE_URL = "https://apic-desktop.musixmatch.com/ws/1.1/macro.subtitles.get?format=json&namespace=lyrics_richsynched&subtitle_format=mxm&app_id=web-desktop-app-v1.0&"
    HEADERS: ClassVar = {
        "authority": "apic-desktop.musixmatch.com",
        "cookie": "mxm_bab=AB",
    }

    @lru_cache(maxsize=5)
    def find_lyrics(
        self,
        *,
        album: str = "",
        artist: str = "",
        title: str = "",
        # duration: str = "",
        # track_id: str = "",
        # subtitle_length: str = "",
    ):
        params = {
            "q_album": album,
            "q_artist": artist,
            "q_track": title,
            # "track_spotify_id": track_id,
            # "q_duration": duration,
            # "f_subtitle_length": subtitle_length,
            "usertoken": self.TOKEN,
        }

        try:
            response = requests.get(self.BASE_URL, params=params, headers=self.HEADERS, timeout=10)
            response.raise_for_status()
        except (requests.RequestException, ConnectionError) as e:
            self.to_screen(repr(e))
            return

        r = response.json()
        if r["message"]["header"]["status_code"] != 200 and r["message"]["header"].get("hint") == "renew":
            self.to_screen("Invalid token")
            return

        body = r["message"]["body"]["macro_calls"]
        status_code = body["matcher.track.get"]["message"]["header"].get("status_code")
        if status_code != 200:
            if status_code == 404:
                self.to_screen("No lyrics/songs found.")
            elif status_code == 401:
                self.to_screen("Timed out.")
            else:
                self.to_screen(f"Requested error: {body['matcher.track.get']['message']['header']}")
            return
        elif isinstance(body["track.lyrics.get"]["message"].get("body"), dict):
            if body["track.lyrics.get"]["message"]["body"]["lyrics"]["restricted"]:
                self.to_screen("Restricted lyrics.")
                return

        return body

    def get_unsynced(self):
        body = self.find_lyrics(artist=self.artist, title=self.title)
        if body is None:
            raise ValueError("No body found")

        lyrics_body = body["track.lyrics.get"]["message"].get("body")
        if lyrics_body is None:
            return None

        lyrics: str = lyrics_body["lyrics"]["lyrics_body"]
        if lyrics:
            return "\n".join(filter(None, lyrics.split("\n")))

        return None

    def get_synced(self):
        body = self.find_lyrics(artist=self.artist, title=self.title)
        if body is None:
            raise ValueError("No body found")

        subtitle_body = body["track.subtitles.get"]["message"].get("body")
        if subtitle_body is None:
            return None
        subtitle = subtitle_body["subtitle_list"][0]["subtitle"]
        if subtitle:
            return "\n".join(
                [
                    f"[{line['time']['minutes']:02d}:{line['time']['seconds']:02d}.{line['time']['hundredths']:02d}]{line['text'] or '♪'}"
                    for line in json.loads(subtitle["subtitle_body"])
                ]
            )

        return None


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
        hasLyrics: bool
        hasTimeSyncedLyrics: bool
        name: str

    class ShazamSongData(TypedDict):
        attributes: ShazamSongAttributes
        id: str
        type: str

    class ShazamSong(TypedDict):
        data: list[ShazamSongData]

    class ShazamSongResult(TypedDict):
        songs: ShazamSong

    class ShazamSearchResult(TypedDict):
        results: ShazamSongResult

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


class ShazamLyricsPlugin(LyricsPluginBase):
    # BASE_URL = "https://www.shazam.com/services/search/v3/en-US/GB/web/search?query={query}&numResults=3&offset=0&types=songs"
    HEADERS: ClassVar = {
        "X-Shazam-Platform": "IPHONE",
        "X-Shazam-AppVersion": "14.1.0",
        "Accept": "*/*",
        "Accept-Language": "en-US",
        "Accept-Encoding": "gzip, deflate",
        "User-Agent": "Mozilla/5.0 (iPad; U; CPU OS 4_3_3 like Mac OS X; en-us) AppleWebKit/533.17.9 (KHTML, like Gecko) Mobile/8J2",
    }

    def __init__(self, info: dict[str, Any], to_screen: Callable[[str], None] | None = None):
        super().__init__(info, to_screen=to_screen)
        self._raw_data: tuple[str, bool, bool] | None = None

    def _make_request(self, url: str) -> requests.Response | None:  # use Any to avoid deep nesting
        try:
            response = requests.get(url, headers=self.HEADERS, allow_redirects=True, timeout=10)
            response.raise_for_status()
        except (requests.RequestException, ConnectionError) as e:
            self.to_screen(repr(e))
            return

        return response

    def _search_for_id(self, query: str, language: str = "GB") -> tuple[str, bool, bool]:
        resp = self._make_request(
            f"https://www.shazam.com/services/amapi/v1/catalog/{language}/search?types=songs&term={query}&limit=3"
        )
        if resp is None:
            raise ValueError("Failed to fetch data")

        data: ShazamSearchResult = resp.json()
        if not data:
            raise ValueError("No data found")

        for song in data["results"]["songs"]["data"]:
            sz_name = song["attributes"]["name"]
            sm = SequenceMatcher(
                lambda x: x in ("-", "_"),
                self.title.lower(),  # type: ignore | if error, damn mypy
                sz_name.lower(),
            )
            ratio = round(sm.ratio(), 2)
            if ratio >= 0.7:
                return (
                    song["id"],
                    song["attributes"]["hasLyrics"],
                    song["attributes"]["hasTimeSyncedLyrics"],
                )

        raise ValueError("No matching song found")

    def _get_real_page(
        self,
        track_id: str,
    ) -> str | None:
        song_page = self._make_request(f"https://www.shazam.com/song/{track_id}")
        if song_page is None:
            return None

        regex = r"<link\s+rel=\"canonical\"\s+href=\"([^\"]+)"
        match = re.search(regex, song_page.text, re.NOFLAG)
        if match:
            return match.group(1)
        return None

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
        if not self.has_lyrics:
            return

        pattern = r'<script type="application/ld\+json">(.*?)</script>'
        match = re.search(pattern, self.page_content, re.DOTALL)
        if not match:
            self.to_screen("Failed to find lyrics data in the song page.")
            return

        json_data = match.group(1).strip()
        data: ShazamPageMusicRecordingCompact = json.loads(json_data)
        lyrics_data = data["recordingOf"]["lyrics"]
        if not lyrics_data:
            self.to_screen("No lyrics found for this track.")
            return

        return lyrics_data["text"]

    def _get_synced_lyrics(self) -> Iterator[str] | None:
        pattern = r"self\.__next_f\.push\(\[\s*1,\s*\"..?:(.*?)\"\]\)"
        match = re.finditer(pattern, self.page_content, re.DOTALL)
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

        # annoying double escape sequences in the JS string, need to decode properly
        lyrics_block_js_string = (
            lyrics_block_js_string.encode()
            .decode("unicode_escape")  # for removing double escape sequences
            .encode("latin-1")  # for correct byte representation
            .decode("utf-8")  # final decode to utf-8
        )

        lyrics_data = json.loads(lyrics_block_js_string)
        if not lyrics_data:
            self.to_screen("No synced lyrics data could be parsed.")
            return

        # lyrics_data[-1]["children"][-1]["children"][-1]["children"][-1][0][-1]["children"][-1][-1]["lyrics"]["lyricLines"]
        lyric_lines_gen = self._deep_search_all(lyrics_data, "lyricLines")
        if TYPE_CHECKING:
            lyric_lines_gen: Iterator[list[dict[str, Any]]]

        for lyric_lines in lyric_lines_gen:
            if isinstance(lyric_lines, list) and len(lyric_lines) > 0:
                for line in lyric_lines:
                    time_raw: str = line.get("startTimeInSeconds", "0")
                    try:
                        time_float = float(time_raw)
                        minutes = int(time_float // 60)
                        seconds = int(time_float % 60)
                        hundredths = int((time_float - int(time_float)) * 100)
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

    def get_synced(self):
        if not self.has_synced_lyrics:
            return

        lyrics_lines = self._get_synced_lyrics()
        if lyrics_lines is None:
            return

        return "\n".join(lyrics_lines)

    @property
    def raw_data(self):
        if not self._raw_data:
            self._raw_data = self._get_data()

        if self._raw_data is None:
            return "", False, False
        return self._raw_data

    @property
    def page_content(self) -> str:
        return self.raw_data[0]

    @property
    def has_lyrics(self) -> bool:
        return self.raw_data[1]

    @property
    def has_synced_lyrics(self) -> bool:
        return self.raw_data[2]

    def _get_data(self) -> tuple[str, bool, bool] | None:
        track_id: str = ""
        has_lyrics: bool = False
        has_synced_lyrics: bool = False

        assert self.title is not None
        query = f"{self.artist} {self.title}" if self.artist else self.title
        for language in ["GB", "JP"]:
            try:
                track_id, has_lyrics, has_synced_lyrics = self._search_for_id(query, language)
            except ValueError as e:
                self.to_screen(repr(e))
                continue

        if not track_id:
            self.to_screen("No matching track found on Shazam.")
            return

        song_url = self._get_real_page(track_id)
        if not song_url:
            self.to_screen("Failed to retrieve the song page.")
            return

        song_page = self._make_request(song_url)
        if song_page is None:
            self.to_screen("Failed to retrieve the song page content.")
            return

        return song_page.text, has_lyrics, has_synced_lyrics


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


class LrcLibLyricsPlugin(LyricsPluginBase):
    BASE_URL = "https://lrclib.net/api"
    HEADERS: ClassVar = {
        "User-Agent": "Mozilla/5.0",
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
            response = requests.get(self.BASE_URL + "/search", params=params, headers=self.HEADERS, timeout=10)
            response.raise_for_status()
            for item in response.json():
                if item.get("track_name") == track_name and item.get("artist_name") == artist_name:
                    return item
            return None

        except (requests.RequestException, ConnectionError) as e:
            self.to_screen(repr(e))
            return

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


def get_lyrics(
    info: dict[str, Any],
    *,
    to_screen: Callable[[str], None] | None = None,
) -> Generator[tuple[Literal["-metadata"], str]]:
    to_screen = to_screen or print

    plugins: list[LyricsPluginBase] = [
        ShazamLyricsPlugin(info, to_screen=to_screen),
        LrcLibLyricsPlugin(info, to_screen=to_screen),
        # MusixMatchLyricsPlugin(info, to_screen=to_screen),  # TODO: fix unath
        YoutubeMusicLyricsPlugin(info, to_screen=to_screen),
    ]

    def save(is_synced: bool, lyrics: str):
        suffix = ".lrc" if is_synced else ".txt"
        _filename = Path(f"{info['filepath']}").with_suffix(suffix)
        _filename.write_text(lyrics, encoding="utf-8")
        to_screen(f"Saved lyrics to {_filename}")

    def embed(is_synced: bool, lyrics: str):
        if is_synced:
            tag = "lyrics"
        tag = "lyrics" if info["ext"] == "opus" else "lyrics-eng"

        yield ("-metadata", f"{tag}={lyrics}")

    def process(is_synced: bool, lyrics: str) -> Generator[tuple[Literal["-metadata"], str], Any, None]:
        if EMBED_LYRICS:
            yield from embed(is_synced, lyrics)
        if SAVE_LRC:
            save(is_synced, lyrics)

    for plugin in plugins if PREFER_SYNCED else []:
        synced_lyrics = plugin.get_synced()
        to_screen(f"Checking synced lyrics with {plugin.__class__.__name__}...")
        if synced_lyrics:
            to_screen("Found synced lyrics.")
            yield from process(True, synced_lyrics)
            return

    for plugin in plugins:
        unsynced_lyrics = plugin.get_unsynced()
        if unsynced_lyrics:
            to_screen("Found unsynced lyrics.")
            yield from process(False, unsynced_lyrics)
            return


### Ugly, but works ¯\_(ツ)_/¯ ###
# apperently this is called monkey patching?
def patched_get_metadata_opts(
    self: FFmpegMetadataPP, info: dict[str, Any]
) -> Generator[
    tuple[Literal["-write_id3v1"], Literal["1"]] | tuple[Literal["-metadata"], str] | tuple[str, str], Any, None
]:
    yield from self.__getattribute__("unpatched_get_metadata_opts")(info)

    # video_id = info.get("id")
    # if not video_id:
    #     self.to_screen("No video ID found")
    #     return

    if not SAVE_LRC and not EMBED_LYRICS:
        return

    try:
        yield from get_lyrics(info, to_screen=self.to_screen)
    except ValueError as e:
        self.to_screen(f"Error getting lyrics: {e}")
        traceback.print_exc()
        return


FFmpegMetadataPP.unpatched_get_metadata_opts = FFmpegMetadataPP._get_metadata_opts  # type: ignore
FFmpegMetadataPP._get_metadata_opts = patched_get_metadata_opts


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

    with yt_dlp.YoutubeDL(options) as _ydl:  # type: ignore
        album_info = _ydl.extract_info(f"https://music.youtube.com/browse/{browse_id}", download=False)
        if not album_info or not isinstance(album_info, dict) or "entries" not in album_info:
            raise ValueError("Failed to get data from album")

        return album_info


@cache
def find_album_id(video_id: str) -> str | None:
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


def find_album_info(video_id: str) -> dict[str, Any]:
    """Find album info from the music URL or video ID."""

    album_browse_id = find_album_id(video_id)
    if not album_browse_id:
        raise ValueError("Failed to fetch album URL")

    return fetch_album_info(album_browse_id)


@cache
def get_track_num_from_album(video_id: str) -> str:
    """Find track number from related album info."""
    album_info = find_album_info(video_id)
    for idx, entry in enumerate(album_info.get("entries", []), start=1):
        if entry.get("id") == video_id:
            return f"{idx:02d}"

    raise ValueError("Video ID not found from the album.")


@cache
def is_various_artist(album_browse_id: str) -> bool:
    album_info = fetch_album_info(album_browse_id)
    if not album_info:
        raise ValueError("Failed to get data from album")

    entries = album_info["entries"]
    first_channel = entries[0].get("channel_id")
    return any(entry.get("channel_id") != first_channel for entry in entries[1:])


# my focking god, stub in pylance so annoying
class CustomMetadataPP(PostProcessor):
    def run(self, information: dict[str, Any]):  # type: ignore[override]
        self.to_screen("Checking metadata...")

        chnl = information.get("channel") or information.get("uploader") or ""
        if chnl.endswith(" - Topic"):
            # Remove duplicate artist names while preserving order
            artists = list(
                OrderedDict.fromkeys(information.get("artists") or information.get("artist", "").split(", "))
            )
            information.update(
                {
                    "artists": artists,
                    "creators": artists,
                    "artist": ", ".join(artists),
                    "creator": ", ".join(artists),
                }
            )

        pl_name: str = information.get("playlist_title") or information.get("playlist") or ""
        if not (pl_name.startswith(("Album - ", "Single - "))):
            self.to_screen("Not an album, getting metadata for album manually")
            try:
                information["track_number"] = get_track_num_from_album(information["id"])
            except ValueError:
                self.to_screen("Hmm, doesn't look like an album. Skipping...")
                # information["track_number"] = None
                return [], information

        try:
            if is_various_artist(find_album_id(information["id"])):
                self.to_screen("Album is a Various Artists compilation")
                information["meta_album_artist"] = "Various Artists"
                # information.setdefault("album_artist", "Various Artists")
        except ValueError:
            self.to_screen("Hmm, doesn't look like an album. Skipping...")
            # information["track_number"] = None

        # Custom logic to handle metadata
        return [], information


ytdl_opts = {
    "extract_flat": False,
    "format": "bestaudio[ext=webm]/bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio/best/b",
    "fragment_retries": 10,
    "ignoreerrors": "only_download",
    "outtmpl": {
        "default": "Album/%(album|Unknown Album)s/%(track_number,playlist_index)02d %(title)s.%(ext)s",
        "pl_thumbnail": "",
    },
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
        {
            "exec_cmd": ["echo {}"],
            "key": "Exec",
            "when": "after_video",
        },
        {
            "exec_cmd": ["echo {}"],
            "key": "Exec",
            "when": "playlist",
        },
    ],
    "extractor_args": {
        "youtube": {
            "lang": ["en"],
            "player_client": ["web"],
        },
        "youtubepot-bgutilhttp": {"base_url": ["https://bgutil-ytdlp-pot-vercal.vercel.app"]},
    },
    "retries": 10,
    "updatetime": False,
    "verbose": True,
    "writethumbnail": True,
}

if "com.termux" in os.environ.get("SHELL", "") or os.environ.get("PREFIX", "") == "/data/data/com.termux/files/usr":
    ytdl_opts["cachedir"] = "$HOME/.config/yt-dlp/"
    ytdl_opts["cookiefile"] = "$HOME/.config/yt-dlp/youtube.com_cookies.txt"
    ytdl_opts["allowed_extractors"] = ["^([yY].*?)([tT]).*e?$"]  # type: ignore
    ytdl_opts["outtmpl"]["default"] = (  # type: ignore
        "/sdcard/Music/%(album|Unknown Album)s/%(track_number,playlist_index)02d %(title)s.%(ext)s"
    )
    # ytdl_opts["extractor_args"]["youtube"]["getpot_bgutil_script"] = (
    #     "$HOME/projects/bgutil-ytdlp-pot-provider/server/build/generate_once.js",
    # )
    ytdl_opts["postprocessors"].extend(  # type: ignore
        [
            {
                "exec_cmd": ["termux-media-scan -r {}"],
                "key": "Exec",
                "when": "playlist",
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
    ytdl_opts["js_runtimes"] = {"node": {}}


def download(url: str, extra_options: dict[str, Any] | None = None):
    options = ytdl_opts.copy()
    if extra_options:
        options.update(extra_options)

    with yt_dlp.YoutubeDL(options) as ydl:  # type: ignore
        ydl.add_post_processor(CustomMetadataPP(), when="pre_process")
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
