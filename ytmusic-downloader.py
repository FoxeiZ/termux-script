#!/data/data/com.termux/files/usr/bin/python

from __future__ import annotations

import os
import subprocess
import sys
from collections import OrderedDict
from typing import Literal

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
):
    cmd = ["termux-notification", "--title", title, "--content", content]
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
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"Error sending notification: {e}")
        return


class CustomMetadataPP(yt_dlp.postprocessor.PostProcessor):
    def __init__(
        self,
        downloader=None,
    ):
        super().__init__(downloader)

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
                information["track_number"] = self.get_track_num_from_album(information)
            except ValueError as e:
                self.to_screen(f"Error getting track number: {e}")

        # Custom logic to handle metadata
        return [], information

    def get_track_num_from_album(self, information) -> str:
        """Find track number from related album info."""
        video_id = information.get("id")
        album_info = self.find_album_info(information)
        for idx, entry in enumerate(album_info.get("entries", []), start=1):
            if entry.get("id") == video_id:
                return f"{idx:02d}"

        raise ValueError("Video ID not found from the album.")

    def find_album_info(self, information) -> dict:
        """Find album info from the music URL or video ID."""

        video_id = information.get("id")
        options = {
            "extract_flat": "in_playlist",
            "format": "best",
            "quiet": True,
            "skip_download": True,
            "source_address": None,
        }

        album_id = self.fetch_album_url(video_id)
        if not album_id:
            raise ValueError("Failed to fetch album URL")

        with yt_dlp.YoutubeDL(options) as _ydl:
            album_info = _ydl.extract_info(
                f"https://music.youtube.com/browse/{album_id}", download=False
            )
            if (
                not album_info
                or not isinstance(album_info, dict)
                or "entries" not in album_info
            ):
                raise ValueError("Failed to get data from album")

            return album_info

    def fetch_album_url(self, video_id: str):
        endpoint = "https://music.youtube.com/youtubei/v1/next?key=AIzaSyDkZV5Q2b1e0Qf4Zc0wRjM3vW3rmpZ_mD0"
        payload = {
            "context": {
                "client": {
                    "clientName": "WEB_REMIX",
                    "clientVersion": "1.20210912.07.00",
                }
            },
            "videoId": video_id,
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://music.youtube.com",
            "Referer": "https://music.youtube.com/",
        }

        response = requests.post(endpoint, headers=headers, json=payload)
        data = response.json()

        # Recursively find the first MPREb ID (albums/singles)
        def find_album_id(obj) -> str | None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == "browseEndpoint" and isinstance(v, dict):
                        browse_id = v.get("browseId")
                        if isinstance(browse_id, str) and browse_id.startswith("MPREb"):
                            return browse_id
                    result = find_album_id(v)
                    if result:
                        return result
            elif isinstance(obj, list):
                for item in obj:
                    result = find_album_id(item)
                    if result:
                        return result
            return None

        return find_album_id(data)


ytdl_opts = {
    "extract_flat": False,
    "format": "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best/b",
    "fragment_retries": 10,
    "ignoreerrors": "only_download",
    "outtmpl": {
        "default": "%(album|Unknown Album)s/%(track_number,playlist_index)02d %(title)s.%(ext)s",
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
        ["$HOME/projects/bgutil-ytdlp-pot-provider/server/build/generate_once.js"],
    )
    ytdl_opts["postprocessors"].append(
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
        if not url.startswith("https://music.youtube.com/watch?v="):
            print(f"Invalid URL: {url}")
            return 1

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
    sys.exit(main("https://music.youtube.com/watch?v=tXb394z4lY8&si=g8oQuDCgB7vKsTGI"))
