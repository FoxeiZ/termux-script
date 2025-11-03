#!/data/data/com.termux/files/usr/bin/python

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import OrderedDict
from functools import cache, lru_cache
from typing import TYPE_CHECKING, Generator, Literal, TypedDict

import requests
import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.postprocessor.ffmpeg import FFmpegMetadataPP
from yt_dlp.postprocessor.metadataparser import MetadataParserPP

if TYPE_CHECKING:
    from typing import Any, Dict


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
    subprocess_timeout: float | None = None,
):
    cmd = ["termux-notification", "--title", title, "--content", content]

    for button, action in [
        (button1, button1_action),
        (button2, button2_action),
        (button3, button3_action),
    ]:
        if action and not button:
            raise ValueError(f"{action} requires the corresponding button to be set")

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
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=subprocess_timeout,
        )

    except Exception as e:
        print(f"Error sending notification: {e}")
        return


class InnerTubeBase:
    if TYPE_CHECKING:
        session: requests.Session

    _instance = None

    API_KEY = "AIzaSyDkZV5Q2b1e0Qf4Zc0wRjM3vW3rmpZ_mD0"
    INNER_TUBE_BASE = "https://music.youtube.com/youtubei/v1"
    HEADERS = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://music.youtube.com",
        "Referer": "https://music.youtube.com/",
    }
    CLIENT_CONTEXT = {
        "client": {"clientName": "WEB_REMIX", "clientVersion": "1.20210912.07.00"}
    }

    def __new__(cls):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance.session = requests.Session()
            cls._instance.session.headers.update(cls.HEADERS)
        return cls._instance

    def fetch(self, endpoint: Literal["next", "browse"], payload: dict) -> dict:
        url = f"{self.INNER_TUBE_BASE}/{endpoint}?key={self.API_KEY}"
        if payload.get("context") is None:
            payload["context"] = self.CLIENT_CONTEXT

        response = self.session.post(
            url, json={**payload, "context": self.CLIENT_CONTEXT}
        )
        response.raise_for_status()
        return response.json()

    @cache
    def fetch_next(self, video_id: str) -> dict:
        return self.fetch("next", {"videoId": video_id})

    @cache
    def fetch_browse(self, browse_id: str):
        return self.fetch("browse", {"browseId": browse_id})


class LyricsPluginBase:
    def __init__(self, info: dict):
        self.video_id = info.get("id")
        self.inner_tube = InnerTubeBase()

    def run(self) -> tuple[bool, str] | None:
        raise NotImplementedError("Subclasses must implement this method")


class YoutubeMusicLyricsPlugin(LyricsPluginBase):
    def run(self):
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

        return False, lyrics_text

    def extract_lyrics_text(self, data):
        try:
            lyrics_runs = data["contents"]["sectionListRenderer"]["contents"][0][
                "musicDescriptionShelfRenderer"
            ]["description"]["runs"]
            return "".join([r["text"] for r in lyrics_runs])
        except (KeyError, IndexError):
            return None

    def extract_lyrics_browse_id(self, data):
        tabs = (
            data.get("contents", {})
            .get("singleColumnMusicWatchNextResultsRenderer", {})
            .get("tabbedRenderer", {})
            .get("watchNextTabbedResultsRenderer", {})
            .get("tabs", [])
        )
        for tab in tabs:
            endpoint = (
                tab.get("tabRenderer", {}).get("endpoint", {}).get("browseEndpoint", {})
            )
            browse_id = endpoint.get("browseId", "")
            if browse_id.startswith("MPLY"):
                return browse_id
        return None


class MusixMatchLyricsPlugin(LyricsPluginBase):
    TOKEN = "2203269256ff7abcb649269df00e14c833dbf4ddfb5b36a1aae8b0"
    BASE_URL = "https://apic-desktop.musixmatch.com/ws/1.1/macro.subtitles.get?format=json&namespace=lyrics_richsynched&subtitle_format=mxm&app_id=web-desktop-app-v1.0&"
    HEADERS = {
        "authority": "apic-desktop.musixmatch.com",
        "cookie": "x-mxm-token-guid=",
    }

    def __init__(self, info: dict):
        super().__init__(info)
        self.title = info.get("title")
        self.artist = info.get("artist") or info.get("uploader")

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
            response = requests.get(self.BASE_URL, params=params, headers=self.HEADERS)
            response.raise_for_status()
        except (requests.RequestException, ConnectionError) as e:
            print(repr(e))
            return

        r = response.json()
        if (
            r["message"]["header"]["status_code"] != 200
            and r["message"]["header"].get("hint") == "renew"
        ):
            print("Invalid token")
            return

        body = r["message"]["body"]["macro_calls"]
        status_code = body["matcher.track.get"]["message"]["header"].get("status_code")
        if status_code != 200:
            if status_code == 404:
                print("No lyrics/songs found.")
            elif status_code == 401:
                print("Timed out.")
            else:
                print(
                    f"Requested error: {body['matcher.track.get']['message']['header']}"
                )
            return
        elif isinstance(body["track.lyrics.get"]["message"].get("body"), dict):
            if body["track.lyrics.get"]["message"]["body"]["lyrics"]["restricted"]:
                print("Restricted lyrics.")
                return

        return body

    def get_unsynced(
        self,
        *,
        album: str = "",
        artist: str = "",
        title: str = "",
    ) -> list[str] | None:
        body = self.find_lyrics(album=album, artist=artist, title=title)
        if body is None:
            raise ValueError("No body found")

        lyrics_body = body["track.lyrics.get"]["message"].get("body")
        if lyrics_body is None:
            return None

        lyrics = lyrics_body["lyrics"]["lyrics_body"]
        if lyrics:
            return [line for line in list(filter(None, lyrics.split("\n")))]

        return None

    def get_synced(
        self,
        *,
        album: str = "",
        artist: str = "",
        title: str = "",
    ):
        body = self.find_lyrics(album=album, artist=artist, title=title)
        if body is None:
            raise ValueError("No body found")

        subtitle_body = body["track.subtitles.get"]["message"].get("body")
        if subtitle_body is None:
            return None
        subtitle = subtitle_body["subtitle_list"][0]["subtitle"]
        if subtitle:
            return [
                f"[{line['time']['minutes']:02d}:{line['time']['seconds']:02d}.{line['time']['hundredths']:02d}]{line['text'] or '♪'}"
                for line in json.loads(subtitle["subtitle_body"])
            ]

        return None

    def run(
        self,
    ):
        if not self.video_id or not self.title or not self.artist:
            return

        for synced, cb in (
            (True, self.get_synced),
            (False, self.get_unsynced),
        ):
            try:
                lyrics = cb(title=self.title, artist=self.artist)
                if lyrics:
                    return synced, "\n".join(lyrics)
            except ValueError:
                print("No lyrics found")
                continue

        return None


LrcLibResponse = TypedDict(
    "LrcLibResponse",
    {
        "id": str,
        "name": str,
        "trackName": str,
        "artistName": str,
        "albumName": str,
        "duration": float,
        "instrumental": bool,
        "plainLyrics": str,
        "syncedLyrics": str,
    },
    total=False,
)


class LrcLibLyricsPlugin(LyricsPluginBase):
    BASE_URL = "https://lrclib.net/api"
    HEADERS = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }

    def __init__(self, info: dict):
        super().__init__(info)
        self.title = info.get("title")
        self.artist = info.get("artist") or info.get("uploader")
        self.album = info.get("album") or info.get("playlist_title") or ""

    @lru_cache(maxsize=5)
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
            response = requests.get(self.BASE_URL + "/search", params=params)
            response.raise_for_status()
            for item in response.json():
                if (
                    item.get("track_name") == track_name
                    and item.get("artist_name") == artist_name
                ):
                    return item
            return None

        except (requests.RequestException, ConnectionError) as e:
            print(repr(e))
            return

    def get_unsynced(
        self,
        *,
        album: str = "",
        artist: str = "",
        title: str = "",
    ) -> str | None:
        body = self.find_lyrics(track_name=title, artist_name=artist, album_name=album)
        if body is None:
            raise ValueError("No body found")

        return body.get("plainLyrics", None)

    def get_synced(
        self,
        *,
        album: str = "",
        artist: str = "",
        title: str = "",
    ) -> str | None:
        body = self.find_lyrics(track_name=title, artist_name=artist, album_name=album)
        if body is None:
            raise ValueError("No body found")

        return body.get("syncedLyrics", None)

    def run(
        self,
    ):
        if not self.video_id or not self.title or not self.artist:
            return

        for synced, cb in (
            (True, self.get_synced),
            (False, self.get_unsynced),
        ):
            try:
                lyrics = cb(title=self.title, artist=self.artist, album=self.album)
                if lyrics:
                    return synced, lyrics
            except ValueError:
                print("No lyrics found")
                continue

        return None


def get_lyrics(info) -> Generator[tuple[Literal["-metadata"], str]]:
    def lyrics_ext_helper(is_synced: bool = False) -> str:
        if is_synced:
            return "lyrics"

        # TODO: fill in later
        if info["ext"] == "opus":
            return "lyrics"
        else:
            return "lyrics-eng"

    for plugin in [
        LrcLibLyricsPlugin,
        MusixMatchLyricsPlugin,
        YoutubeMusicLyricsPlugin,
    ]:
        if not issubclass(plugin, LyricsPluginBase):
            raise TypeError("Invalid plugin type")

        lyrics_plugin = plugin(info)
        result = lyrics_plugin.run()
        if not result:
            continue

        if isinstance(result, tuple) and len(result) == 2:
            is_synced, lyrics_text = result
            yield ("-metadata", f"{lyrics_ext_helper(is_synced)}={lyrics_text}")
            return
    return


### Ugly, but works ¯\_(ツ)_/¯ ###
# apperently this is called monkey patching?
def Patched_get_metadata_opts(self: FFmpegMetadataPP, info):
    yield from self.__getattribute__("Unpatched_get_metadata_opts")(info)  # type: ignore[no-untyped-call]

    # video_id = info.get("id")
    # if not video_id:
    #     self.to_screen("No video ID found")
    #     return

    try:
        yield from get_lyrics(info)
    except ValueError as e:
        self.to_screen(f"Error getting lyrics: {e}")
        return


setattr(
    FFmpegMetadataPP,
    "Unpatched_get_metadata_opts",
    FFmpegMetadataPP._get_metadata_opts,
)
setattr(
    FFmpegMetadataPP,
    "_get_metadata_opts",
    Patched_get_metadata_opts,
)


@cache
def fetch_album_info(browse_id: str | None) -> dict:
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
        album_info = _ydl.extract_info(
            f"https://music.youtube.com/browse/{browse_id}", download=False
        )
        if (
            not album_info
            or not isinstance(album_info, dict)
            or "entries" not in album_info
        ):
            raise ValueError("Failed to get data from album")

        return album_info


@cache
def find_album_id(video_id: str) -> str | None:
    data = InnerTubeBase().fetch_next(video_id)

    # Recursively find the first MPREb ID (albums/singles)
    def find(obj) -> str | None:
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


def find_album_info(video_id: str) -> dict:
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
    for entry in entries[1:]:
        if entry.get("channel_id") != first_channel:
            return True
    return False


# my focking god, stub in pylance so annoying
class CustomMetadataPP(PostProcessor):
    def run(self, information: Dict[str, Any]):  # type: ignore[override]
        self.to_screen("Checking metadata...")  # type: ignore

        chnl = information.get("channel") or information.get("uploader") or ""
        if chnl.endswith(" - Topic"):
            # Remove duplicate artist names while preserving order
            artists = list(
                OrderedDict.fromkeys(
                    information.get("artists")
                    or information.get("artist", "").split(", ")
                )
            )
            information.update(
                {
                    "artists": artists,
                    "creators": artists,
                    "artist": ", ".join(artists),
                    "creator": ", ".join(artists),
                }
            )

        pl_name: str = (
            information.get("playlist_title") or information.get("playlist") or ""
        )
        if not (pl_name.startswith("Album - ") or pl_name.startswith("Single - ")):
            self.to_screen("Not an album, getting metadata for album manually")  # type: ignore
            try:
                information["track_number"] = get_track_num_from_album(
                    information["id"]
                )
            except ValueError:
                self.to_screen("Hmm, doesn't look like an album. Skipping...")  # type: ignore
                # information["track_number"] = None
                return [], information

        try:
            if is_various_artist(find_album_id(information["id"])):  # type: ignore
                self.to_screen("Album is a Various Artists compilation")  # type: ignore
                information["meta_album_artist"] = "Various Artists"
                # information.setdefault("album_artist", "Various Artists")
        except ValueError:
            self.to_screen("Hmm, doesn't look like an album. Skipping...")  # type: ignore
            # information["track_number"] = None

        # Custom logic to handle metadata
        return [], information


ytdl_opts = {
    "extract_flat": False,
    "format": "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best/b",
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
    ],
    "extractor_args": {
        "youtube": {
            "lang": ["en"],
            "player_client": ["mweb"],
        },
        "youtubepot-bgutilhttp": {
            "base_url": ["https://bgutil-ytdlp-pot-vercal.vercel.app"]
        },
    },
    "retries": 10,
    "updatetime": False,
    "verbose": True,
    "writethumbnail": True,
}

if (
    "com.termux" in os.environ.get("SHELL", "")
    or os.environ.get("PREFIX", "") == "/data/data/com.termux/files/usr"
):
    ytdl_opts["cachedir"] = "$HOME/.config/yt-dlp/"
    # ytdl_opts["cookiefile"] = "/storage/emulated/0/mpv/youtube.com_cookies.txt"
    ytdl_opts["outtmpl"]["default"] = (
        "/sdcard/Music/%(album|Unknown Album)s/%(track_number,playlist_index)02d %(title)s.%(ext)s"
    )
    # ytdl_opts["extractor_args"]["youtube"]["getpot_bgutil_script"] = (
    #     "$HOME/projects/bgutil-ytdlp-pot-provider/server/build/generate_once.js",
    # )
    ytdl_opts["postprocessors"].extend(
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


def download(url: str, extra_options: dict | None = None):
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
    sys.exit(main("https://music.youtube.com/watch?v=peORBtRz_vs"))
