from pathlib import Path
from html.parser import HTMLParser
import mimetypes
from torf import Torrent
import requests


def get_type(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mime.split("/")[0]
    return "folder"


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

        if data == "Name:":
            self.name_hit = True
            return

        if self.name_hit:
            self.name = data
            self.name_hit = False
            return

        if not self.hit:
            return

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

    @property
    def name(self) -> str:
        return self.torrent.name

    def build(self):
        file: Path
        for file in self.torrent.files:
            if file.is_dir():
                self.folders.append(file.name)
            else:
                self.folders.extend(
                    folder for folder in file.parts[:-1] if folder not in self.folders
                )

                match get_type(file):
                    case "image":
                        self.images.append(file.name)
                    case "video":
                        self.videos.append(file.name)
                    case _:
                        pass


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
        match get_type(path):
            case "folder":
                print(path.name)
                print("huh?")
            case "image":
                self.images.append(path.name)
            case "video":
                self.videos.append(path.name)
            case _:  # default
                self.files.append(path.name)

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


def compare(obj_1, obj_2, calc_diff: bool = True, calc_dup: bool = True):
    for _type in ("folders", "images", "videos"):
        if calc_diff:
            diff_1 = set(getattr(obj_1, _type)) - set(getattr(obj_2, _type))
            print(
                f"\nDifferent {obj_1.name} / {obj_2.name} {_type}:",
                diff_1,
                f"The number of different {_type}: {len(diff_1)}",
                sep="\n",
            )
            diff_2 = set(getattr(obj_2, _type)) - set(getattr(obj_1, _type))
            print(
                f"\nDifferent {obj_2.name} / {obj_1.name} {_type}:",
                diff_2,
                f"The number of different {_type}: {len(diff_2)}",
                sep="\n",
            )

        if calc_dup:
            duplicate = set(getattr(obj_1, _type)) & set(getattr(obj_2, _type))
            print(
                f"\nDuplicate {obj_1.name} / {obj_2.name} {_type}:",
                duplicate,
                f"The number of duplicate {_type}: {len(duplicate)}",
                sep="\n",
            )


def get_btdig(url: str):
    req = requests.get(url)
    req.raise_for_status()

    html = req.text
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
        "https://btdig.com/b05fc8c4d7e545083f269e49360ba1cc5a5c5601/%E7%8C%AB%E7%88%AA%E5%91%B8%E7%BD%97"
    )
    local = get_local("D:\\qBittorent\\download\\猫爪呸罗呸罗2024粉丝圈")
    torrent = get_torrent("D:\\qBittorent\\torrent\\猫と爪呸罗呸罗.torrent")
    compare(torrent, btdig, calc_diff=True, calc_dup=False)
