from __future__ import annotations

import mimetypes
import re
from html.parser import HTMLParser
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING

import requests
from torf import Torrent

if TYPE_CHECKING:
    from typing import Literal


def get_type(
    path: Path | PathLike | str,
) -> Literal["folder", "image", "video", "audio", "file", "unknown"]:
    if isinstance(path, (PathLike, str)):
        path = Path(path)

    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mime.split("/")[0]  # type: ignore

    if path.is_file():
        if path.exists() and path.lstat().st_size == 0:
            return "unknown"

        if path.suffix in (".jpg", ".jpeg", ".png", ".gif"):
            return "image"
        if path.suffix in (".mp4", ".mkv", ".webm"):
            return "video"
        return "file"

    return "unknown"


def clean_string(
    string: str, remove: bool = False, replace: bool = False, str_replace: str = ""
) -> str:
    pattern = r"2048\.cc@|SaveTwitter\.Net - |\(\d*p\)|_save"
    if remove:
        return re.sub(pattern, "", string)

    if replace:
        return re.sub(pattern, str_replace, string)

    return string


class BtDigParser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.folders: list[str] = []
        self.images: list[str] = []
        self.videos: list[str] = []
        self.files: list[str] = []

        self.type = ""
        self.hit = False
        self.name_hit = False

        self.name = ""

    def handle_data(self, data: str) -> None:
        data = data.strip()
        if not data:
            return

        if data == "Name:":
            self.name_hit = True
            return

        if self.name_hit:
            self.name = data
            self.name_hit = False
            return

        if not self.hit:
            return

        data = clean_string(data, remove=True)
        if self.type == "files":
            match get_type(Path(data)):
                case "image":
                    self.images.append(data)
                case "video":
                    self.videos.append(data)
                case _:
                    self.files.append(data)
        else:
            getattr(self, self.type).append(data)

        self.hit = False

    def handle_div(self, attrs):
        params = dict(attrs)
        if "class" not in params:
            return

        class_type_map = {
            "fa-folder-open": "folders",
            "fa-file-image": "images",
            "fa-file-video": "videos",
            "fa-file": "files",
        }

        if "fa" not in params["class"]:
            return

        for class_name, type_name in class_type_map.items():
            if class_name in params["class"]:
                self.type = type_name
                self.hit = True
                return

    def handle_starttag(self, tag, attrs):
        if tag == "div":
            self.handle_div(attrs)

    def print(self):
        print("Folders:")
        for folder in self.folders:
            print(folder)

        print("\nImages:")
        for image in self.images:
            print(image)

        print("\nVideos:")
        for file in self.videos:
            print(file)


class TorrentParser:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.torrent = Torrent.read(str(self.path))

        self.folders: list[str] = []
        self.images: list[str] = []
        self.videos: list[str] = []
        self.files: list[str] = []

    @property
    def name(self) -> str:
        return self.torrent.name or self.path.name

    def build(self):
        file: PathLike
        for file in self.torrent.files:
            name = clean_string(file.name, remove=True)

            if file.is_dir():
                self.folders.append(name)

            else:
                self.folders.extend(
                    folder for folder in file.parts[:-1] if folder not in self.folders
                )

                match get_type(file):
                    case "image":
                        self.images.append(name)
                    case "video":
                        self.videos.append(name)
                    case _:
                        self.files.append(name)


class LocalParser:
    def __init__(self, path: Path | str):
        self.path = Path(path)

        self.folders: list[str] = []
        self.images: list[str] = []
        self.videos: list[str] = []
        self.files: list[str] = []

    @property
    def name(self) -> str:
        return self.path.name

    def build_file(self, path: Path) -> None:
        name = clean_string(path.name, remove=True)
        match _type := get_type(path):
            case "image":
                self.images.append(name)
            case "video":
                self.videos.append(name)
            case "file":
                self.files.append(name)
            case _:
                print(_type, name, end=" ")
                if path.is_file() and path.lstat().st_size == 0:
                    return print("empty file")

    def build(self, path: Path | str | None = None) -> None:
        if not path:
            path = self.path

        if isinstance(path, str):
            path = Path(path)

        if path.name not in self.folders:
            self.folders.append(path.name)

        for subpath in path.iterdir():
            if subpath.is_dir():
                self.build(subpath)
            else:
                self.build_file(subpath)

    def print(self):
        print("Folders:")
        for folder in self.folders:
            print(folder)

        print("\nImages:")
        for image in self.images:
            print(image)

        print("\nVideos:")
        for file in self.videos:
            print(file)


def compare(
    obj_1, obj_2, calc_diff: bool = True, calc_dup: bool = True, two_way: bool = False
):
    for _type in ("folders", "images", "videos"):
        set1 = set(getattr(obj_1, _type))
        set2 = set(getattr(obj_2, _type))

        if calc_diff:
            diff1 = set1 - set2
            print(f"\nDifferent {obj_1.name} / {obj_2.name} {_type}:")
            print(diff1)
            print(f"The number of different {_type}: {len(diff1)}")

            if two_way:
                diff2 = set2 - set1
                print(f"\nDifferent {obj_2.name} / {obj_1.name} {_type}:")
                print(diff2)
                print(f"The number of different {_type}: {len(diff2)}")

        if calc_dup:
            dups = set1 & set2
            print(f"\nDuplicate {obj_1.name} / {obj_2.name} {_type}:")
            print(dups)
            print(f"The number of duplicate {_type}: {len(dups)}")


def get_btdig(url: str):
    req = requests.get(url)
    req.raise_for_status()

    html = req.text
    parser = BtDigParser()
    parser.feed(html)
    return parser


def get_btdig_html(html: str):
    parser = BtDigParser()
    parser.feed(html)
    return parser


def get_local(path: Path | str):
    torrent = LocalParser(path)
    torrent.build()
    return torrent


def get_torrent(path: Path | str):
    torrent = TorrentParser(path)
    torrent.build()
    return torrent


if __name__ == "__main__":
    btdig = get_btdig(
        r"https://btdig.com/834095a6dc2a4a649126f8f491b82e6de81f5092/%E7%8C%AB%E7%88%AA"
    )
    local = get_local("D:\\qBittorent\\download\\猫爪呸罗呸罗2024粉丝圈")
    # btdig_html = get_btdig_html("猫と爪呸罗呸罗 torrent.html")
    # torrent = get_torrent("D:\\qBittorent\\torrent\\猫と爪呸罗呸罗.torrent")
    compare(btdig, local, calc_diff=True, calc_dup=False, two_way=False)
