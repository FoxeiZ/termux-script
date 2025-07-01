from __future__ import annotations

import asyncio
import json
import re
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from pathlib import Path
from threading import Event, Lock, Semaphore
from time import sleep
from typing import TYPE_CHECKING, Any, TypeVar, cast
from urllib.parse import urlparse

try:
    import xmltodict
    from bs4 import BeautifulSoup, Tag
    from cloudscraper import CloudScraper
    from quart import Quart, Response, redirect, request
    from quart.utils import run_sync
except ImportError as e:
    raise ImportError(
        "Required libraries are not installed. Please install the required packages: "
        "bs4, cloudscraper, quart, xmltodict."
    ) from e

from lib.plugins import Plugin
from lib.utils import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import List, Optional, TypedDict

    from requests import Response as RequestResponse

    class _GalleryImageInfo(TypedDict):
        """Type definition for gallery image data."""

        t: str
        w: int
        h: int

    GalleryPage = GalleryCover = GalleryThumbnail = _GalleryImageInfo

    class GalleryImage(TypedDict):
        """Type definition for gallery image data."""

        pages: List[GalleryPage]
        cover: GalleryCover
        thumbnail: GalleryThumbnail

    class NhentaiGallery(TypedDict):
        """Type definition for nhentai gallery data from JSON."""

        id: int
        media_id: str
        title: str
        scanlator: str
        artists: List[str]
        writers: List[str]
        upload_date: int
        tags: List[str]
        language: str
        category: str
        parodies: List[str]
        characters: List[str]
        images: GalleryImage
        page_count: int
        num_pages: int
        num_favorites: int


IMAGE_TYPE_MAPPING = {
    "j": "jpg",
    "p": "png",
    "w": "webp",
    "g": "gif",
}
SUPPORTED_IMAGE_TYPES = set(IMAGE_TYPE_MAPPING.values())


logger = get_logger("n_server_proxy")
app = Quart(__name__)


class _Singleton(type):
    """A metaclass that creates a Singleton base class when called."""

    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(_Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class Singleton(_Singleton(str("SingletonMeta"), (object,), {})):
    pass


V = TypeVar("V")
K = TypeVar("K")


class LRUCache(OrderedDict[K, V]):
    def __init__(self, max_size):
        super().__init__()
        self.max_size = max_size

    def get(self, key: K) -> V | None:  # type: ignore
        if key not in self:
            return None
        else:
            self.move_to_end(key)
            return self[key]

    def put(self, key: K, value: V) -> None:
        self[key] = value
        self.move_to_end(key)
        if len(self) > self.max_size:
            self.popitem(last=False)

    def remove(self, key: K) -> None:
        if key in self:
            del self[key]


class GalleryInfoCache(LRUCache[int, "NhentaiGallery"], Singleton):
    """Cache for BeautifulSoup objects to avoid re-parsing the same HTML content."""

    def __init__(self, max_size: int = 4):
        super().__init__(max_size=max_size)


class ResourceCache(LRUCache[str, tuple[dict, bytes]], Singleton):
    """Cache for resources to avoid re-fetching the same content."""

    def __init__(self, max_size: int = 100):
        super().__init__(max_size=max_size)


def clean_manga_title(manga_title):
    edited_title = re.sub(r"\[.*?]", "", manga_title).strip()
    edited_title = re.sub(r"\(.*?\)", "", edited_title).strip()
    edited_title = re.sub(r"\{.*?\}", "", edited_title).strip()

    while True:
        if "|" in edited_title:
            edited_title = re.sub(r".*\|", "", edited_title).strip()
        else:
            break

    return edited_title


class FileStatus(Enum):
    CONVERTED = "converted"
    COMPLETED = "completed"
    MISSING = "missing"
    NOT_FOUND = "not_found"


def check_file_status(
    gallery_id: int | None = None,
    gallery_title: str | None = None,
    gallery_info: NhentaiGallery | None = None,
) -> FileStatus:
    """Check if a gallery is already downloaded based on its ID or information."""
    if gallery_id is None and gallery_title is None:
        if gallery_info:
            gallery_id = gallery_info["id"]
            gallery_title = gallery_info["title"]
    if gallery_id is None or gallery_title is None:
        raise ValueError("gallery_id and gallery_title must be provided.")

    gallery_path = Path(f"{app.config['GALLERY_PATH']}/{gallery_title}")
    if not gallery_path.exists():
        return FileStatus.NOT_FOUND

    cbz_path = gallery_path / f"{gallery_id}.cbz"
    if cbz_path.exists():
        return FileStatus.CONVERTED

    total_images = len(gallery_info["images"]["pages"]) if gallery_info else 0
    if total_images == 0:
        return FileStatus.MISSING

    test_name = tuple(
        f"{img_idx}.{IMAGE_TYPE_MAPPING.get(image['t'], 'jpg')}"
        for img_idx, image in enumerate(gallery_info["images"]["pages"], start=1)  # type: ignore
    )
    if all((gallery_path / name).exists() for name in test_name):
        return FileStatus.COMPLETED

    return FileStatus.MISSING


class DownloadStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    FAILED = "failed"


@dataclass
class DownloadProgress:
    gallery_id: int
    total_images: int
    downloaded_images: int = 0
    failed_images: int = 0
    status: DownloadStatus = DownloadStatus.PENDING

    @property
    def progress_percentage(self) -> float:
        if self.total_images == 0:
            return 0.0
        return (self.downloaded_images / self.total_images) * 100

    @property
    def is_complete(self) -> bool:
        return self.downloaded_images == self.total_images


class DownloadPool(Singleton):
    """A pool for managing download tasks."""

    def __init__(self, max_workers: int = 1):
        super().__init__()
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._requester = Requests()
        self._progress: dict[int, DownloadProgress] = dict()
        self._progress_lock = Lock()

    def _download(self, info: "NhentaiGallery"):
        gallery_title = info["title"]
        gallery_id = info["id"]
        logger.info(
            "Downloading images for gallery '%s' ID: %d", gallery_title, gallery_id
        )

        # Update status to downloading
        with self._progress_lock:
            if gallery_id in self._progress:
                self._progress[gallery_id].status = DownloadStatus.DOWNLOADING

        gallery_path = Path(f"{app.config['GALLERY_PATH']}/{gallery_title}")
        if not gallery_path.exists():
            gallery_path.mkdir(parents=True, exist_ok=True)

        for img_idx, image in enumerate(info["images"]["pages"], start=1):
            image_type = IMAGE_TYPE_MAPPING.get(image.get("t", "j"))
            future = self._pool.submit(
                self._download_image,
                f"https://i{{idx_server}}.nhentai.net/galleries/{info['media_id']}/{img_idx}.{image_type}",
                gallery_path / f"{img_idx}.{image_type}",
            )
            future.add_done_callback(
                lambda fut, gid=gallery_id: self._on_download_complete(fut, gid)
            )

    def _on_download_complete(self, future, gallery_id: int):
        """Callback for when a download task is completed."""
        with self._progress_lock:
            if gallery_id not in self._progress:
                return

            progress = self._progress[gallery_id]

            if future.exception():
                logger.error(
                    "Download failed for gallery ID %d: %s",
                    gallery_id,
                    future.exception(),
                )
                progress.failed_images += 1
            else:
                progress.downloaded_images += 1

            # Check if download is complete
            if (
                progress.downloaded_images + progress.failed_images
                >= progress.total_images
            ):
                if progress.failed_images == 0:
                    logger.info("All images downloaded for gallery ID %d", gallery_id)
                    # Remove completed downloads from progress tracking
                    self._progress.pop(gallery_id, None)
                else:
                    progress.status = DownloadStatus.FAILED
                    logger.warning(
                        "Download completed with errors for gallery ID %d: %d/%d failed",
                        gallery_id,
                        progress.failed_images,
                        progress.total_images,
                    )

    def _download_image(self, url: str, path: Path):
        if path.exists():
            logger.info("Image already exists: %s", path)
            return

        for idx_server in range(1, 10):
            formatted_url = url.format(idx_server=idx_server)
            logger.info("Downloading image from %s to %s", formatted_url, path)
            try:
                r = self._requester.get(formatted_url, stream=True, timeout=30)
                if r.status_code == 200:
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    logger.info("Successfully downloaded: %s", formatted_url)
                    sleep(0.1)
                    return
                else:
                    logger.warning(
                        "Failed to download image from %s: %s",
                        formatted_url,
                        r.status_code,
                    )
            except Exception as e:
                logger.error("Error downloading from %s: %s", formatted_url, e)
                continue

        raise Exception(f"Failed to download from all servers: {url}")

    def shutdown(self):
        """Shutdown the download pool and wait for all tasks to complete."""
        logger.info("Shutting down download pool...")
        self._pool.shutdown(wait=True)
        logger.info("Download pool shutdown complete.")

    def save_cbz(self, info: "NhentaiGallery", remove_images: bool = True):
        gallery_path = Path(f"{app.config['GALLERY_PATH']}/{info['title']}")
        if not gallery_path.exists():
            logger.error("Gallery path does not exist: %s", gallery_path)
            return

        file_path = gallery_path / f"{info['id']}.cbz"
        if file_path.exists():
            logger.info("CBZ file already exists: %s", file_path)
            return

        with zipfile.ZipFile(file_path, "w") as cbz_zip:
            total_images = 0
            for img_file in gallery_path.iterdir():
                if (
                    img_file.is_file()
                    and img_file.suffix.lower().lstrip(".") in SUPPORTED_IMAGE_TYPES
                ):
                    cbz_zip.write(img_file, img_file.name)
                    total_images += 1
                    if remove_images:
                        img_file.unlink()

            with cbz_zip.open("ComicInfo.xml", "w") as f:
                if info["characters"]:
                    info["characters"].insert(0, "#field-characters")
                    info["characters"].append("#end-field-characters")

                f.write(
                    xmltodict.unparse(
                        {
                            "ComicInfo": {
                                "@xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
                                "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                                "Title": info["title"],
                                "Series": info["title"],
                                "Number": 1,  # Assuming single volume # TODO: Handle multi-volume
                                "LanguageISO": (
                                    "en"
                                    if info["language"] == "english"
                                    else "ja"
                                    if info["language"] == "japanese"
                                    else "zh"
                                    if info["language"] == "chinese"
                                    else "unknown"
                                ),
                                "PageCount": total_images,
                                "Penciller": ", ".join(info["artists"]),
                                "Writer": ", ".join(info["writers"]),
                                "Translator": info["scanlator"],
                                "Tags": ", ".join(info["tags"]),
                                "Genre": ", ".join(
                                    info["characters"]
                                ),  # since characters are not available in ComicInfo.xml, use them as genre
                                "SeriesGroup": ", ".join(info["parodies"]),
                                "Web": f"https://nhentai.net/g/{info['id']}",
                            }
                        },
                        pretty=True,
                    ).encode("utf-8")
                )

    def add(self, info: "NhentaiGallery"):
        """Submit a download task for the given gallery information."""
        gallery_id = info["id"]

        # Check file status before starting download
        file_status = check_file_status(gallery_info=info)

        # If already converted, don't download
        if file_status == FileStatus.CONVERTED:
            logger.info(
                "Gallery ID %d is already converted to CBZ, skipping download.",
                gallery_id,
            )
            # No need to track progress for already converted files
            return

        # If already completed, just convert to CBZ
        if file_status == FileStatus.COMPLETED:
            logger.info(
                "Gallery ID %d is already downloaded, converting to CBZ.", gallery_id
            )
            # No need to track progress, just convert
            self._pool.submit(self.save_cbz, info)
            return

        # Check if already downloading
        if self.is_downloading(gallery_id):
            logger.info(
                "Gallery ID %d is already being downloaded, skipping submission.",
                gallery_id,
            )
            return

        total_images = len(info["images"]["pages"])

        with self._progress_lock:
            # Initialize progress tracking
            self._progress[gallery_id] = DownloadProgress(
                gallery_id=gallery_id,
                total_images=total_images,
                status=DownloadStatus.PENDING,
            )

        self._pool.submit(self._download, info).add_done_callback(
            fn=lambda _, info=info: self._pool.submit(self.save_cbz, info)
        )

    def get_progress(self, gallery_id: int) -> Optional[DownloadProgress]:
        """Get download progress for a specific gallery."""
        with self._progress_lock:
            return self._progress.get(gallery_id)

    def get_all_progress(self) -> dict[int, DownloadProgress]:
        """Get download progress for all galleries."""
        with self._progress_lock:
            return self._progress.copy()

    def remove_progress(self, gallery_id: int):
        """Remove progress tracking for a gallery (useful for cleanup)."""
        with self._progress_lock:
            self._progress.pop(gallery_id, None)

    def is_downloading(self, gallery_id: int) -> bool:
        """Check if a gallery is currently being downloaded."""
        with self._progress_lock:
            progress = self._progress.get(gallery_id)
            return progress is not None and progress.status in [
                DownloadStatus.PENDING,
                DownloadStatus.DOWNLOADING,
            ]


class Requests(Singleton, CloudScraper):
    def __init__(self):
        super(Requests, self).__init__(
            browser={
                "browser": "chrome",
                "platform": "windows",
                "mobile": False,
            },
            delay=10,
            debug=False,
        )
        self._sema = Semaphore(1)

    def _clean_headers(self, headers: dict[str, Any]) -> None:
        """Remove headers that may cause issues with proxying."""
        # Remove headers that are not needed or may cause issues
        headers.pop("Host", None)
        headers.pop("Accept-Encoding", None)
        headers.pop("Content-Length", None)

    def request(self, *args, **kwargs):
        if "headers" in kwargs:
            headers = kwargs["headers"]
            if isinstance(headers, dict):
                # Remove Host and Accept-Encoding headers to avoid issues with proxying
                self._clean_headers(headers)

        with self._sema:
            return super().request(*args, **kwargs)


class ModifyRule(Singleton):
    def __init__(self):
        super().__init__()
        self.rules: OrderedDict[str, Callable[[BeautifulSoup, str], None]] = (
            OrderedDict()
        )

    @classmethod
    def add_rule(cls, pattern: str):
        """Add a modification rule to the cache."""

        def wrapper(
            func: Callable[[BeautifulSoup, str], None],
        ) -> Callable[[BeautifulSoup, str], None]:
            instance = cls()
            instance.rules[pattern] = func
            logger.info("Added modification rule: %s -> %s", pattern, func.__name__)
            return func

        return wrapper

    def modify(self, url: str, soup: BeautifulSoup, html_content: str) -> str:
        """Modify HTML content using registered rules."""
        for pattern, func in self.rules.items():
            if re.search(pattern, url):
                logger.info("Applying rule: %s", pattern)
                func(soup, html_content)
        return str(soup)


def parse_tags_from_html(html: str) -> List[str]:
    """Parse tag names from HTML content, extracting tag names from class attributes."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        tags = []
        tag_links = soup.find_all("a", class_=re.compile(r"tag tag-\d+"))

        for tag_link in tag_links:
            if not isinstance(tag_link, Tag):
                continue

            name_span = tag_link.find("span", class_="name")
            if isinstance(name_span, Tag):
                tag_name = name_span.get_text(strip=True)
                if tag_name:
                    tags.append(tag_name)

        return tags

    except Exception as e:
        print(f"Error parsing tags from HTML: {e}")
        return []


def parse_chapter(html: str) -> Optional[NhentaiGallery]:
    """Parse HTML content to extract gallery information from JSON data and tag details from HTML."""
    pattern = re.compile(r"window\._gallery = JSON\.parse\(\"([^\"]+)\"\);")
    match = pattern.search(html)
    if not match:
        return None

    json_string = match.group(1)
    json_string = json_string.encode().decode("unicode_escape")
    gallery_data: dict = json.loads(json_string)

    gallery_data["title"] = clean_manga_title(
        gallery_data["title"].get("english", "")
        or gallery_data["title"].get("japanese", "")
        or gallery_data["title"].get("pretty", "Unknown Title")
    )

    original_tags = cast(list, gallery_data.get("tags", [])).copy()
    tags = gallery_data["tags"] = []
    artists = gallery_data["artists"] = []
    writers = gallery_data["writers"] = []
    parodies = gallery_data["parodies"] = []
    characters = gallery_data["characters"] = []
    for tag in original_tags:
        if tag["type"] == "tag":
            tags.append(tag["name"])
        elif tag["type"] == "artist":
            artists.append(tag["name"])
        elif tag["type"] == "parody":
            parodies.append(tag["name"])
        elif tag["type"] == "language":
            gallery_data["language"] = tag["name"]
        elif tag["type"] == "category":
            gallery_data["category"] = tag["name"]
        elif tag["type"] == "group":
            writers.append(tag["name"])
        elif tag["type"] == "character":
            characters.append(tag["name"])
        else:
            logger.warning(f"Unknown tag type: {tag['type']} with name: {tag['name']}")

    return cast("NhentaiGallery", gallery_data)


@ModifyRule.add_rule(r"/g/\d+")
def modify_chapter(soup: BeautifulSoup, html_content: str) -> None:
    gallery_id_h3 = soup.find("h3", id="gallery_id")
    if not gallery_id_h3:
        logger.warning("No gallery ID found in the HTML content.")
        return
    gallery_id = gallery_id_h3.text.strip().lstrip("#")

    gallery_data = parse_chapter(html_content)
    if not gallery_data:
        logger.warning("No gallery data found in the HTML content.")
        return
    GalleryInfoCache().put(gallery_data["id"], gallery_data)

    btn_container = soup.find("div", class_="buttons")
    if not btn_container:
        logger.warning("No button container found in the HTML content.")
        return
    if not isinstance(btn_container, Tag):
        raise TypeError("Expected btn_container to be a BeautifulSoup Tag")

    js_script = """
let progressInterval = null;

function addGallery(event, galleryId) {
    event.preventDefault();
    const addButton = document.getElementById("add");
    
    addButton.classList.add("btn-disabled");
    addButton.innerText = "Adding...";

    fetch(`/add/${galleryId}`, {
        method: 'GET',
        headers: {
            'Content-Type': 'application/json',
        },
    }).then(response => response.json())
    .then(data => {
        if (data.message === "Download started" || data.message === "Gallery is already being downloaded") {
            addButton.innerText = "Pending...";
            startProgressMonitoring(galleryId);
        } else {
            alert('Failed to add gallery.');
            addButton.classList.remove("btn-disabled");
            addButton.innerText = "Add";
        }
    }).catch(error => {
        console.error('Error:', error);
        alert('Failed to add gallery.');
        addButton.classList.remove("btn-disabled");
        addButton.innerText = "Add";
    });
}

function startProgressMonitoring(galleryId) {
    if (progressInterval) {
        clearInterval(progressInterval);
    }
    
    updateProgress(galleryId);
    progressInterval = setInterval(() => updateProgress(galleryId), 1000);
}

function updateProgress(galleryId) {
    fetch(`/progress/${galleryId}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                // No progress found - check if download completed
                fetch(`/p/nhentai.net/g/${galleryId}`)
                    .then(() => {
                        // If we can fetch the page, check file status by reloading
                        const addButton = document.getElementById("add");
                        addButton.innerText = "Added";
                        stopProgressMonitoring();
                        setTimeout(() => {
                            window.location.reload();
                        }, 1000);
                    })
                    .catch(() => {
                        // Something went wrong
                        stopProgressMonitoring();
                    });
                return;
            }
            
            const addButton = document.getElementById("add");
            const percentage = Math.round(data.progress_percentage);
            
            switch(data.status) {
                case "pending":
                    addButton.innerText = "Pending...";
                    break;
                case "downloading":
                    addButton.innerText = `Downloading...${percentage}%`;
                    break;
                case "failed":
                    addButton.innerText = `Failed (${data.failed_images}/${data.total_images})`;
                    addButton.style.color = "#ff6b6b";
                    stopProgressMonitoring();
                    break;
            }
        })
        .catch(error => {
            console.error('Progress fetch error:', error);
        });
}

function stopProgressMonitoring() {
    if (progressInterval) {
        clearInterval(progressInterval);
        progressInterval = null;
    }
}

// Check if download is already in progress on page load
document.addEventListener('DOMContentLoaded', function() {
    const galleryId = parseInt(document.getElementById('gallery_id').innerText.replace('#', ''));
    fetch(`/progress/${galleryId}`)
        .then(response => response.json())
        .then(data => {
            if (!data.error && (data.status === "pending" || data.status === "downloading")) {
                const addButton = document.getElementById("add");
                addButton.classList.add("btn-disabled");
                
                if (data.status === "pending") {
                    addButton.innerText = "Pending...";
                } else {
                    const percentage = Math.round(data.progress_percentage);
                    addButton.innerText = `Downloading...${percentage}%`;
                }
                
                startProgressMonitoring(galleryId);
            }
        })
        .catch(error => {
            // No progress found, that's fine
        });
});
"""
    soup.head.append(soup.new_tag("script", string=js_script))  # type: ignore

    def create_download():
        _a = soup.new_tag(
            "a",
            attrs={
                "class": "btn btn-secondary",
                "id": "download",
                "href": f"/download/{gallery_id}",
            },
        )
        _a.string = "Download "

        _i = soup.new_tag(
            "i",
            attrs={"class": "fa fa-download"},
        )
        _a.append(_i)
        return _a

    def create_add():
        # Use the file status check function
        file_status = check_file_status(gallery_info=gallery_data)
        pool = DownloadPool()
        is_downloading = pool.is_downloading(gallery_data["id"])

        # Determine button state based on file status and download status
        if file_status == FileStatus.CONVERTED:
            button_text = "Converted"
            button_class = "btn btn-primary btn-disabled"
            button_icon = "fa fa-check"
            is_disabled = True
        elif file_status == FileStatus.COMPLETED:
            button_text = "Downloaded"
            button_class = "btn btn-info btn-disabled"
            button_icon = "fa fa-check"
            is_disabled = True
        elif is_downloading:
            button_text = "Downloading..."
            button_class = "btn btn-primary btn-disabled"
            button_icon = "fa fa-spinner fa-spin"
            is_disabled = True
        else:
            button_text = "Add"
            button_class = "btn btn-primary"
            button_icon = "fa fa-plus"
            is_disabled = False

        _a = soup.new_tag(
            "a",
            attrs={
                "class": button_class,
                "id": "add",
                "style": "min-width: unset; padding: 0 0.75rem",
                **(
                    {
                        "href": "#",
                        "onclick": f"addGallery(event, {gallery_id});",
                    }
                    if not is_disabled
                    else {}
                ),
            },
        )
        _a.string = f"{button_text} "

        _i = soup.new_tag(
            "i",
            attrs={"class": button_icon},
        )
        _a.append(_i)
        return _a

    def create_image_proxy():
        _a = soup.new_tag(
            "a",
            attrs={
                "class": "btn btn-secondary",
                "id": "image-proxy",
                "href": f"/p/nhentai.net/g/{gallery_id}?proxy_images={'0' if request.args.get('proxy_images', '0') == '1' else '1'}",
            },
        )
        _a.string = "Image Proxy "

        _i = soup.new_tag(
            "i",
            attrs={"class": "fa fa-image"},
        )
        _a.append(_i)
        return _a

    btn_container.clear()
    btn_container.append(create_add())
    btn_container.append(create_download())
    btn_container.append(soup.new_tag("br"))
    btn_container.append(create_image_proxy())
    logger.info("Modified button to download gallery.")


@ModifyRule.add_rule(r"nhentai\.net")
def modify_gallery(soup: BeautifulSoup, _: str) -> None:
    logger.info("Modifying gallery page content")

    for gallery_div in soup.find_all("div", class_="gallery"):
        if not isinstance(gallery_div, Tag):
            continue

        a = gallery_div.find("a", class_="cover")
        caption = gallery_div.find("div", class_="caption")
        if (
            not a
            or not isinstance(a, Tag)
            or not caption
            or not isinstance(caption, Tag)
        ):
            continue

        gallery_id = cast(str, a.get("href") or "").rstrip("/").split("/")[-1]
        gallery_title = clean_manga_title(caption.get_text(strip=True))
        if not gallery_id.isdigit():
            logger.warning("Invalid gallery ID found in the HTML content.")
            continue

        file_status = check_file_status(
            gallery_title=gallery_title,
            gallery_id=int(gallery_id),
        )
        if file_status == FileStatus.NOT_FOUND:
            logger.warning(
                "Gallery %s ID %s not found in the filesystem.",
                gallery_title,
                gallery_id,
            )
            continue

        a.img["style"] = "opacity: 0.7;"  # type: ignore
        _div = soup.new_tag(
            "div",
            attrs={
                "class": "btn btn-secondary",
                "style": "position: absolute; display: block; pointer-events: none;",
            },
        )
        if file_status == FileStatus.CONVERTED:
            _div.string = "Converted"
        elif file_status == FileStatus.COMPLETED:
            _div.string = "Downloaded"
        elif file_status == FileStatus.MISSING:
            _div.string = "Partial | In library"
        a.append(_div)


def modify_html_content(html_content: str, base_url: str, proxy_base: str) -> str:
    """Modify HTML content to inject custom elements and fix relative URLs"""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        tags_attr = [
            ("a", "href"),
            # ("img", "src"),
            # ("img", "data-src"),
            ("link", "href"),
            ("script", "src"),
            ("form", "action"),
        ]
        if request.args.get("proxy_images", "0") == "1":
            tags_attr.append(("img", "src"))
            tags_attr.append(("img", "data-src"))

        for tag_attr in tags_attr:
            tag_name, attr_name = tag_attr
            for tag in soup.find_all(tag_name, {attr_name: True}):
                if not isinstance(tag, Tag):
                    continue
                url = tag[attr_name]
                if (
                    not url
                    or not isinstance(url, str)
                    or url.startswith(
                        (
                            "javascript:",
                            "data:",
                            "mailto:",
                            "tel:",
                            "..",
                        )
                    )
                ):
                    continue

                if url.startswith(("http://", "https://")):
                    parts = urlparse(url)
                    tag[attr_name] = (
                        f"{proxy_base}p/{parts.netloc}/{parts.path.lstrip('/')}"
                        f"{parts.query and '?' + parts.query or ''}"
                        f"{parts.fragment and '#' + parts.fragment or ''}"
                    )
                else:
                    tag[attr_name] = f"/p/{base_url}/{url.lstrip('/')}"
                logger.info("Modified %s to: %s", url, tag[attr_name])

        return ModifyRule().modify(request.url, soup, html_content)

    except Exception as e:
        logger.error("Failed to parse HTML content: %s", e)
        return html_content


@app.route("/add/<int:_id>", methods=["GET"])
async def add(_id: int):
    if not _id:
        return "No ID provided for add.", 400

    pool = DownloadPool()

    info = GalleryInfoCache().get(_id)
    if not info:
        return redirect(f"/p/nhentai.net/g/{_id}")

    if "images" not in info or not info["images"].get("pages"):
        GalleryInfoCache().remove(_id)
        return redirect(f"/p/nhentai.net/g/{_id}")

    file_status = check_file_status(gallery_info=info)
    if file_status in [FileStatus.CONVERTED, FileStatus.COMPLETED]:
        return {
            "message": f"Gallery already {file_status.value}",
            "gallery_id": _id,
            "status": file_status.value,
        }, 200

    # Check if already downloading
    if pool.is_downloading(_id):
        return {
            "message": "Gallery is already being downloaded",
            "gallery_id": _id,
        }, 200

    pool.add(info)

    return {
        "message": "Download started",
        "gallery_id": _id,
        "total_images": len(info["images"]["pages"]),
    }, 200


@app.route("/progress/<int:gallery_id>", methods=["GET"])
async def get_progress(gallery_id: int):
    """Get download progress for a specific gallery."""
    pool = DownloadPool()
    progress = pool.get_progress(gallery_id)

    if not progress:
        return {"error": "No download progress found for this gallery"}, 404

    return {
        "gallery_id": progress.gallery_id,
        "total_images": progress.total_images,
        "downloaded_images": progress.downloaded_images,
        "failed_images": progress.failed_images,
        "status": progress.status.value,
        "progress_percentage": progress.progress_percentage,
        "is_complete": progress.is_complete,
    }


@app.route("/p/<path:url>", methods=["GET", "POST"])
async def proxy(url: str):
    """Fetches the specified URL and streams it out to the client.
    If the request was referred by the proxy itself (e.g. this is an image fetch
    for a previously proxied HTML page), then the original Referer is passed."""
    # Check if url to proxy has host only, and redirect with trailing slash
    # (path component) to avoid breakage for downstream apps attempting base
    # path detection
    # url_parts = urlparse("%s://%s" % (request.scheme, url))
    # if url_parts.path == "":
    #     parts = urlparse(request.url)
    #     logger.warning(
    #         "Proxy request without a path was sent, redirecting assuming '/': %s -> %s/"
    #         % (url, url)
    #     )
    #     return redirect(urlunparse(parts._replace(path=parts.path + "/")))
    cacher = ResourceCache()

    if rc := cacher.get(url):
        logger.info("Resource already cached: %s", url)
        return Response(
            rc[1],
            headers=rc[0],
            status=200,
        )
    logger.warning("Make request to %s %s", request.method, url)
    data = await request.form if request.method == "POST" else None
    r: RequestResponse = await run_sync(
        lambda: Requests().request(
            request.method,
            "https://" + url,
            params=request.args,
            headers=dict(request.headers),
            allow_redirects=False,
            data=data,
            timeout=10,
            stream=True,
        ),
    )()
    logger.warning("Got %s response from %s", r.status_code, url)

    headers = dict(r.headers)
    headers.pop("Content-Encoding", None)
    headers.pop("Transfer-Encoding", None)
    headers.pop("Content-Length", None)
    content_type = r.headers.get("Content-Type", "")
    if "text/html" in content_type:
        parts = urlparse(r.url)
        return Response(
            modify_html_content(r.text, parts.netloc, request.host_url),
            headers=headers,
            status=r.status_code,
        )

    def generate_response():
        _headers = headers.copy()
        _headers.pop("Content-Security-Policy", None)
        _headers.pop("X-Content-Security-Policy", None)
        _headers["Access-Control-Allow-Origin"] = "*"

        is_good = r.status_code == 200
        cache = BytesIO()
        for chunk in r.iter_content(4096):
            yield chunk
            if is_good:
                cache.write(chunk)
        cache.seek(0)
        if is_good:
            cacher.put(url, (_headers, cache.getvalue()))

    return Response(
        generate_response(),
        headers={
            **headers,
            "Access-Control-Allow-Origin": "*",
        },
        status=r.status_code,
    )


@app.route("/<path:url>", methods=["GET", "POST"])
async def root(url: str):
    """Redirects relative paths to the proxy URL."""
    # If referred from a proxy request, then redirect to a URL with the proxy prefix.
    # This allows server-relative and protocol-relative URLs to work.
    referer = request.headers.get("referer")
    if not referer:
        return Response(status=404)

    netloc = referer.split("/p/")[-1].split("/")[0]
    redirect_url = f"/p/{netloc}/{url}"
    if request.query_string:
        redirect_url += f"?{request.query_string.decode('utf-8')}"
    logger.debug("Redirecting relative path to one under proxy: %s", redirect_url)
    return Response(status=302, headers={"Location": redirect_url})


class ServerProxyPlugin(Plugin):
    def __init__(
        self,
        *args,
        port: int = 5000,
        host: str = "0.0.0.0",
        debug: bool = False,
        gallery_path: str | Path = "galleries",
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.host = host
        self.port = port
        self.debug = debug
        gallery_path = Path(gallery_path)
        if not gallery_path.exists():
            gallery_path.mkdir(parents=True, exist_ok=True)
        self.gallery_path = gallery_path.resolve()
        app.config["GALLERY_PATH"] = str(gallery_path)

        self._wait_for_shutdown = Event()
        self._internal_shutdown_event = asyncio.Event()
        self._shutdown_event = asyncio.Event()
        self._loop = None

    async def _wait_internal_shutdown(self):
        """Wait for the internal shutdown event to be set."""
        await self._internal_shutdown_event.wait()
        self.logger.info("Internal shutdown event triggered")
        await app.shutdown()

    def start(self) -> None:
        """Start the ServerProxyPlugin app."""
        try:
            # Get or create event loop
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)

            # Create and start the server task
            server_task = self._loop.create_task(
                app.run_task(
                    host=self.host,
                    port=self.port,
                    debug=self.debug,
                    shutdown_trigger=self._shutdown_event.wait,  # type: ignore[call-arg]
                )
            )
            stop_task = self._loop.create_task(self._wait_internal_shutdown())
            self.logger.info(
                "ServerProxyPlugin started on http://%s:%d", self.host, self.port
            )
            self.send_success(
                content=f"Server is running on http://{self.host}:{self.port}",
                title="ServerProxyPlugin started",
            )
            self._loop.run_until_complete(
                asyncio.gather(
                    server_task,
                    stop_task,
                )
            )

        except Exception as e:
            self.logger.error("Failed to start ServerProxyPlugin: %s", e)
            raise
        finally:
            if self._loop and not self._loop.is_closed():
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
                self._loop.close()
                self.logger.info("Event loop closed")

        self._wait_for_shutdown.set()

    def stop(self) -> None:
        """Shutdown the ServerProxyPlugin app and clean up resources."""
        self.logger.info("Shutting down ServerProxyPlugin...")

        try:
            DownloadPool().shutdown()
            GalleryInfoCache().clear()

            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._shutdown_event.set)
                self._internal_shutdown_event.set()
                self._shutdown_event.set()

        except Exception as e:
            self.logger.error("Error during ServerProxyPlugin shutdown: %s", e)

        self._wait_for_shutdown.wait()
        self.logger.info("ServerProxyPlugin has been fully stopped.")
