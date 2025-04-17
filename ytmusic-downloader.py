#!/data/data/com.termux/files/usr/bin/python

from __future__ import annotations

import os
import subprocess
import sys
from collections import OrderedDict
from functools import cache
from typing import TYPE_CHECKING, Literal

import requests
import yt_dlp


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


def extract_lyrics_text(data):
    try:
        lyrics_runs = data["contents"]["sectionListRenderer"]["contents"][0][
            "musicDescriptionShelfRenderer"
        ]["description"]["runs"]
        return "".join([r["text"] for r in lyrics_runs])
    except (KeyError, IndexError):
        return None


def extract_lyrics_browse_id(data):
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


def lyrics_ext_mapping(ext: str) -> str:
    # TODO: fill in later
    if ext == "opus":
        return "lyrics"
    else:
        return "lyrics-eng"


### Ugly, but works ¯\_(ツ)_/¯ ###
def Patched_get_metadata_opts(self: yt_dlp.postprocessor.FFmpegMetadataPP, info):
    yield from self.Unpatched_get_metadata_opts(info)  # type: ignore[no-untyped-call]

    video_id = info.get("id")
    if not video_id:
        self.to_screen("No video ID found")
        return

    if not (info.get("channel") or info.get("uploader") or "").endswith(" - Topic"):
        self.to_screen("Not a music-only ID, skipping lyrics metadata")
        return

    inner_tube = InnerTubeBase()
    data = inner_tube.fetch_next(video_id)
    browse_id = extract_lyrics_browse_id(data)

    if not browse_id:
        self.to_screen("No lyrics browse ID found")
        return

    lyrics_data = inner_tube.fetch_browse(browse_id)
    lyrics_text = lyrics_data and extract_lyrics_text(lyrics_data)
    if not lyrics_text:
        self.to_screen("No lyrics text found")
        return

    yield "-metadata", f"lyrics={lyrics_text}"


setattr(
    yt_dlp.postprocessor.FFmpegMetadataPP,
    "Unpatched_get_metadata_opts",
    yt_dlp.postprocessor.FFmpegMetadataPP._get_metadata_opts,
)
setattr(
    yt_dlp.postprocessor.FFmpegMetadataPP,
    "_get_metadata_opts",
    Patched_get_metadata_opts,
)


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

    with yt_dlp.YoutubeDL(options) as _ydl:
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


class CustomMetadataPP(yt_dlp.postprocessor.PostProcessor):
    def run(self, information):
        self.to_screen("Checking metadata...")

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
            self.to_screen("Not an album, getting metadata for album manually")
            try:
                information["track_number"] = get_track_num_from_album(
                    information["id"]
                )
            except ValueError:
                self.to_screen("Hmm, doesn't look like an album. Skipping...")
                # information["track_number"] = None
                return [], information

        try:
            if is_various_artist(find_album_id(information["id"])):  # type: ignore
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
                    yt_dlp.postprocessor.metadataparser.MetadataParserPP.interpretter,
                    "",
                    "(?P<meta_synopsis>)",
                ),
                (
                    yt_dlp.postprocessor.metadataparser.MetadataParserPP.interpretter,
                    "",
                    "(?P<meta_date>)",
                ),
                (
                    yt_dlp.postprocessor.metadataparser.MetadataParserPP.replacer,
                    "meta_artist",
                    " - Topic$",
                    "",
                ),
                (
                    yt_dlp.postprocessor.metadataparser.MetadataParserPP.interpretter,
                    "artist",
                    "(?P<meta_album_artist>.*)",
                ),
                (
                    yt_dlp.postprocessor.metadataparser.MetadataParserPP.replacer,
                    "meta_album_artist",
                    "[,/&].+",
                    "",
                ),
                (
                    yt_dlp.postprocessor.metadataparser.MetadataParserPP.interpretter,
                    "%(track_number,playlist_index|01)s",
                    "%(track_number)s",
                ),
                (
                    yt_dlp.postprocessor.metadataparser.MetadataParserPP.interpretter,
                    "%(album,playlist_title|Unknown Album)s",
                    "%(album)s",
                ),
                (
                    yt_dlp.postprocessor.metadataparser.MetadataParserPP.replacer,
                    "album",
                    "^Album - ",
                    "",
                ),
                (
                    yt_dlp.postprocessor.metadataparser.MetadataParserPP.interpretter,
                    "%(genre|Unknown Genre)s",
                    "%(genre)s",
                ),
                (
                    yt_dlp.postprocessor.metadataparser.MetadataParserPP.interpretter,
                    "description",
                    "(?P<meta_date>(?<=Released on: )\\d{4})",
                ),
                (
                    yt_dlp.postprocessor.metadataparser.MetadataParserPP.interpretter,
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
            "player_client": ["web"],
        }
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
    ytdl_opts["cookiefile"] = "/storage/emulated/0/mpv/youtube.com_cookies.txt"
    ytdl_opts["outtmpl"]["default"] = (
        "/sdcard/Music/%(album|Unknown Album)s/%(track_number,playlist_index)02d %(title)s.%(ext)s"
    )
    ytdl_opts["extractor_args"]["youtube"]["getpot_bgutil_script"] = (
        "$HOME/projects/bgutil-ytdlp-pot-provider/server/build/generate_once.js",
    )
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

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.add_post_processor(CustomMetadataPP(), when="pre_process")
        ydl.download([url])


def main(url: str | None = None) -> int | None:
    if not url:
        if len(sys.argv) < 2:
            print("Usage: termux-url-opener.py <url>")
            return 1
        url = sys.argv[1]

    try:
        download(url)
    except Exception as e:
        print(f"Download error: {e}")
        notify(
            title=e.__class__.__name__,
            content=str(e),
            id="download_error",
            # action="termux-open-url",
            button1="OK",
            button1_action="termux-notification-remove --id download_error",
        )
        return 1


if __name__ == "__main__":
    sys.exit(
        main(
            "https://music.youtube.com/playlist?list=OLAK5uy_m4TsxT05t8zKD3cM7TbRcwuN1_HHhJsMg&si=7tGZGOmzyrN6x5db"
        )
    )
    # sys.exit(main("https://music.youtube.com/watch?v=8UVNT4wvIGY&si=YD4i0G0IpW9-h0jH"))
