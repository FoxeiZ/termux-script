import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any, Union
import subprocess
from threading import Lock

try:
    import langcodes
    import xmltodict
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "langcodes", "xmltodict"])
finally:
    import langcodes
    import xmltodict


FORCE = False  # Force re-parse
LOG_LEVEL = 3  # 0: Error, 1: Warning, 2: Info, 3: Debug
SIMULATE = False  # Do not write to disk
FAVORITE_THRESHOLD = 2000  # 2000 favorites = 5.0 rating
SKIP_THRESHOLD = 20  # Skip if found this many already parsed
FIX_ONLY = False  # Only apply fixes, also implies FORCE

# Apply fixes
MOVE_CHARACTERS_TO_GENRE = True
FIX_MULTIPLE_VALUES = True
FIX_DUPLICATE_SUMMARY = True


class ZFile(zipfile.ZipFile):
    def remove(self, zinfo_or_arcname):
        """Remove a member from the archive."""

        if self.mode not in ("w", "x", "a"):
            raise ValueError("remove() requires mode 'w', 'x', or 'a'")
        if not self.fp:
            raise ValueError("Attempt to write to ZIP archive that was already closed")
        if self._writing:  # type: ignore
            raise ValueError(
                "Can't write to ZIP archive while an open writing handle exists"
            )

        # Make sure we have an existing info object
        if isinstance(zinfo_or_arcname, zipfile.ZipInfo):
            zinfo = zinfo_or_arcname
            # make sure zinfo exists
            if zinfo not in self.filelist:
                raise KeyError("There is no item %r in the archive" % zinfo_or_arcname)
        else:
            # get the info object
            zinfo = self.getinfo(zinfo_or_arcname)

        return self._remove_members({zinfo})

    def _remove_members(self, members, *, remove_physical=True, chunk_size=2**20):
        """Remove members in a zip file.
        All members (as zinfo) should exist in the zip; otherwise the zip file
        will erroneously end in an inconsistent state.
        """
        fp = self.fp
        if not fp:
            raise ValueError("Attempt to write to ZIP archive that was already closed")

        entry_offset = 0
        member_seen = False

        # get a sorted filelist by header offset, in case the dir order
        # doesn't match the actual entry order
        filelist = sorted(self.filelist, key=lambda x: x.header_offset)
        for i in range(len(filelist)):
            info = filelist[i]
            is_member = info in members

            if not (member_seen or is_member):
                continue

            # get the total size of the entry
            try:
                offset = filelist[i + 1].header_offset
            except IndexError:
                offset = self.start_dir
            entry_size = offset - info.header_offset

            if is_member:
                member_seen = True
                entry_offset += entry_size

                # update caches
                self.filelist.remove(info)
                try:
                    del self.NameToInfo[info.filename]
                except KeyError:
                    pass
                continue

            # update the header and move entry data to the new position
            if remove_physical:
                old_header_offset = info.header_offset
                info.header_offset -= entry_offset
                read_size = 0
                while read_size < entry_size:
                    fp.seek(old_header_offset + read_size)
                    data = fp.read(min(entry_size - read_size, chunk_size))
                    fp.seek(info.header_offset + read_size)
                    fp.write(data)
                    fp.flush()
                    read_size += len(data)

        # Avoid missing entry if entries have a duplicated name.
        # Reverse the order as NameToInfo normally stores the last added one.
        for info in reversed(self.filelist):
            self.NameToInfo.setdefault(info.filename, info)

        # update state
        if remove_physical:
            self.start_dir -= entry_offset
        self._didModify = True

        # seek to the start of the central dir
        fp.seek(self.start_dir)


class SkipThresholdReached(Exception):
    pass


class ThresholdCounter:
    def __init__(self, threshold: int):
        self._threshold = threshold
        self._counter = 0
        self._skip = False

    def skip_increment(self):
        self._skip = True

    def is_threshold_reached(self):
        return self._threshold >= 0 and self._counter >= self._threshold

    def increment(self):
        if self._skip:
            self._skip = False
            return

        if self.is_threshold_reached():
            raise SkipThresholdReached
        self._counter += 1


threshold_counter = ThresholdCounter(SKIP_THRESHOLD)


class colors:
    reset = "\033[0m"
    bold = "\033[01m"
    disable = "\033[02m"
    underline = "\033[04m"
    reverse = "\033[07m"
    strikethrough = "\033[09m"
    invisible = "\033[08m"

    class fg:
        black = "\033[30m"
        red = "\033[31m"
        green = "\033[32m"
        orange = "\033[33m"
        blue = "\033[34m"
        purple = "\033[35m"
        cyan = "\033[36m"
        lightgrey = "\033[37m"
        darkgrey = "\033[90m"
        lightred = "\033[91m"
        lightgreen = "\033[92m"
        yellow = "\033[93m"
        lightblue = "\033[94m"
        pink = "\033[95m"
        lightcyan = "\033[96m"

    class bg:
        black = "\033[40m"
        red = "\033[41m"
        green = "\033[42m"
        orange = "\033[43m"
        blue = "\033[44m"
        purple = "\033[45m"
        cyan = "\033[46m"
        lightgrey = "\033[47m"


class cprint:
    lock = Lock()

    @staticmethod
    def _to_string(*args: str | list | dict, delimiter=" ") -> str:
        return delimiter.join(map(str, args))

    @staticmethod
    def _base_print(color: str, delimiter: str, *args: str, **kwargs):
        with cprint.lock:
            print(color + cprint._to_string(*args, delimiter) + colors.reset, **kwargs)

    @staticmethod
    def info(*args: str, delimiter=" ", **kwargs):
        if LOG_LEVEL < 2:
            return
        cprint._base_print(
            colors.fg.lightblue,
            delimiter,
            *args,
            **kwargs,
        )

    @staticmethod
    def error(*args: str, delimiter=" ", **kwargs):
        if LOG_LEVEL < 0:
            return
        cprint._base_print(
            colors.fg.red,
            delimiter,
            *args,
            **kwargs,
        )

    @staticmethod
    def success(*args: str, delimiter=" ", **kwargs):
        cprint._base_print(
            colors.fg.green,
            delimiter,
            *args,
            **kwargs,
        )

    @staticmethod
    def warning(*args: str, delimiter=" ", **kwargs):
        if LOG_LEVEL < 1:
            return
        cprint._base_print(
            colors.fg.yellow,
            delimiter,
            *args,
            **kwargs,
        )

    @staticmethod
    def debug(*args: str, delimiter=" ", **kwargs):
        if LOG_LEVEL < 3:
            return
        cprint._base_print(
            colors.fg.darkgrey,
            delimiter,
            *args,
            **kwargs,
        )


class ComicParser:
    title: str
    series: str
    number: int
    count: int
    volume: int
    alternate_series: str
    alternate_number: int
    alternate_count: int
    summary: str
    notes: str
    year: int
    month: int
    day: int
    writer: str
    penciller: str
    inker: str
    colorist: str
    letterer: str
    cover_artist: str
    editor: str
    translator: str
    publisher: str
    imprint: str
    genre: str
    tags: str
    web: str
    format: str
    ean: str
    black_white: str
    manga: str
    characters: str
    teams: str
    locations: str
    scan_information: str
    story_arc: str
    story_arc_number: int
    series_group: str
    age_rating: str
    main_character_or_team: str
    review: str
    language_iso: str
    community_rating: float
    added: str
    released: str
    file_size: int
    file_modified_time: str
    file_creation_time: str
    book_price: str
    custom_values_store: str

    def __init__(self, path: Union[Path, str]):
        self.path = Path(path)
        self.is_closed = False
        # self.__other_files: dict[str, bytes] = {}
        self.__other_fields: dict[str, str] = {}
        self.page_count = 0

        self.__unpack_zip(self.path)

    def __unpack_zip(self, path: Union[Path, str]):
        def __info(items: dict) -> dict:
            copycat = items.copy()
            content = {}
            fields = {
                k: ("".join(i.title() for i in k.split("_")), v)
                for k, v in self.__annotations__.items()
            }
            for key, (field_key, field_type) in fields.items():
                if field_key in items:
                    setattr(self, key, field_type(items[field_key]))
                    copycat.pop(field_key)
                else:
                    setattr(self, key, self.default_attr(field_type))
            self.__other_fields.update(copycat)
            return content

        try:
            with ZFile(path, "r", zipfile.ZIP_STORED) as zf:
                names = zf.namelist()
                names.remove("ComicInfo.xml")
                with zf.open("ComicInfo.xml", "r") as f:
                    comic_info = xmltodict.parse(f.read()).get("ComicInfo", {})
                    __info(items=comic_info)
                self.page_count = len(names)

        except Exception as e:
            cprint.error(f"Failed to unpack {path}: {e}")
            return None

    def save(self, output_path: Path):
        if LOG_LEVEL >= 3:
            cprint.debug(self.to_dict())

        if SIMULATE:  # check here in case of fix-only = true and simulate = true
            return

        def __info():
            content = {}
            fields = {
                "".join(i.title() for i in k.split("_")): k
                for k, _ in self.__annotations__.items()
            }

            for field_key, key in fields.items():
                value = getattr(self, key)
                if value and value != -1 and value != "":
                    content[field_key] = value

            content.update(
                {
                    "@xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
                    "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                    "PageCount": self.page_count,
                    **self.__other_fields,
                }
            )
            return content

        # output_path = output_path / self.path.parent.name / self.path.name if output_path else self.path
        # if output_path != self.path:
        #     output_path.parent.mkdir(parents=True, exist_ok=True)
        #     shutil.copy2(self.path, output_path)

        with ZFile(output_path, "a", zipfile.ZIP_STORED) as zf:
            zf.remove("ComicInfo.xml")
            with zf.open("ComicInfo.xml", "w") as f:
                f.write(
                    xmltodict.unparse({"ComicInfo": __info()}, pretty=True).encode()
                )

    @staticmethod
    def default_attr(value: Any) -> Any:
        if value in (int, float):
            return -1
        elif isinstance(value, bool):
            return False
        elif isinstance(value, str):
            return ""
        else:
            return value()

    def to_dict(self) -> dict:
        return {
            k: v
            for k in self.__annotations__
            if not k.startswith("__") and (v := getattr(self, k)) not in ("", None)
        }


def clean_manga_title(manga_title):
    edited_title = re.sub(r"\[.*?]", "", manga_title).strip()
    edited_title = re.sub(r"\(.*?\)", "", edited_title).strip()
    edited_title = re.sub(r"\{.*?\}", "", edited_title).strip()

    while True:
        if "|" in edited_title:
            edited_title = re.sub(r".*\|", "", edited_title).strip()
        else:
            break

    if manga_title != edited_title:
        cprint.debug(f"Cleaned title: {manga_title} -> {edited_title}")

    return edited_title


def fix_summary(comic_parser: ComicParser):
    first_len = len(comic_parser.summary)
    comic_parser.summary = "\n".join(set(comic_parser.summary.split("\n")))
    second_len = len(comic_parser.summary)
    if first_len != second_len:
        cprint.info("Fixed duplicate summary")


def fix_multiple_values(comic_parser: ComicParser):
    for key, value in comic_parser.__dict__.items():
        if "__" not in key and isinstance(value, str) and " | " in value:
            setattr(comic_parser, key, ", ".join(set(value.split(" | "))))


def fix_characters_to_genre(comic_parser: ComicParser):
    if comic_parser.characters != "":
        cprint.info(f"Copy characters field to genre: {comic_parser.characters}")
        comic_parser.genre = (
            f"#field-characters, {comic_parser.characters}, #end-field-characters"
        )


def calc_rating(rating: str) -> float:
    cprint.debug(f"Rating: {rating}")
    int_rating = int(rating.strip())
    return min((int_rating / FAVORITE_THRESHOLD) * 5, 5.0)


def remove_extra_fields(comic_parser: ComicParser) -> ComicParser:
    if (
        "#field-characters" in comic_parser.genre
        or "#end-field-characters" in comic_parser.genre
    ):
        cprint.warning(f"{comic_parser.path}: Contains #field-characters")
        idx_start = comic_parser.genre.index("#field-characters") + len(
            "#field-characters, "
        )
        idx_end = comic_parser.genre.index("#end-field-characters") - 2
        comic_parser.genre = comic_parser.genre[idx_start:idx_end].strip()


def parse_tag_v1(comic_parser: ComicParser) -> ComicParser | None:
    remove_extra_fields(comic_parser)
    comic_parser.tags = comic_parser.genre
    comic_parser.genre = ""

    summaries = comic_parser.summary.split("\n")
    new_summaries = []
    comic_parser.summary = ""
    for summary in summaries:
        s0, s1 = summary.split(": ", 1) if ": " in summary else (summary, "")
        if s1:
            s1 = s1.strip().replace(" | ", ", ")

        if "Language" == s0:
            if (lang := langcodes.find(s1)).is_valid():
                comic_parser.language_iso = lang.to_tag()
        elif "Pages" == s0 or "Categories" == s0:
            pass
        elif "Favorited by" == s0:  # Community Rating
            comic_parser.community_rating = calc_rating(s1) if s1.isdigit() else 0.0
        elif "Parodies" == s0:
            comic_parser.series_group = s1.title()
        elif "Characters" == s0:
            comic_parser.characters = s1
        elif "Group" == s0:
            comic_parser.writer = s1
        elif "Artist" == s0:
            comic_parser.penciller = s1
        else:
            new_summaries.append(summary)

    comic_parser.summary = "\n".join(new_summaries)

    return comic_parser


def parse_tag_v2(comic_parser: ComicParser) -> ComicParser | None:
    genres = comic_parser.genre.split(", ")

    # reset metadata
    comic_parser.genre = ""
    comic_parser.summary = ""

    new_metadata: dict[str, list[str]] = {}
    new_summaries = []
    for genre in genres:
        key, value = genre.split(": ", 1)
        if key not in new_metadata:
            new_metadata[key] = []
        new_metadata[key].extend(value.split(" | "))

    for key, value in new_metadata.items():
        match key:
            case "tag":
                comic_parser.tags = ", ".join(value)
            case "language":
                for v in value:
                    if (lang := langcodes.find(v)).is_valid():
                        comic_parser.language_iso = lang.to_tag()  # type: ignore
                        break
            case "parody":
                comic_parser.series_group = ", ".join(value).title()
            case "group":
                comic_parser.writer = ", ".join(value)
            case "artist":
                comic_parser.penciller = ", ".join(value)
            case "character":
                comic_parser.characters = ", ".join(value)
            case _:
                new_summaries.append(f"\n{key}: {', '.join(value)}")

    comic_parser.summary = "\n".join(new_summaries)

    return comic_parser


def apply_fixes(comic_parser: ComicParser, save: bool = False) -> ComicParser:
    is_apply = False
    if FIX_MULTIPLE_VALUES:
        fix_multiple_values(comic_parser)
        is_apply = True

    if MOVE_CHARACTERS_TO_GENRE:
        fix_characters_to_genre(comic_parser)
        is_apply = True

    if FIX_DUPLICATE_SUMMARY:
        fix_summary(comic_parser)
        is_apply = True

    if is_apply:
        cprint.info(f"Applied fixes to {comic_parser.path}")
        if save:
            comic_parser.save(comic_parser.path)

    return comic_parser


def parse_cbz(file_path: Path, output_path: Path | None = None) -> None:
    comic_from_cbz = ComicParser(file_path)
    if comic_from_cbz is None:
        return cprint.error(f"Failed to access {file_path}")

    if comic_from_cbz.series == "":
        return cprint.error(f"Failed to obtain metadata from {file_path}")

    if comic_from_cbz.tags == "" and comic_from_cbz.genre == "":
        return cprint.warning(f"Skipping {file_path}: No tags or genre found")

    if FIX_ONLY:
        apply_fixes(comic_from_cbz, save=True)
        return

    if comic_from_cbz.notes == "parsed" and not FORCE:
        cprint.debug(f"Metadata already parsed for {file_path}")
        apply_fixes(comic_from_cbz, save=True)
        threshold_counter.increment()
        return

    if comic_from_cbz.genre != "" and comic_from_cbz.genre.count(": ") > 1:
        cprint.debug(f"Using v2 parser for {file_path}")
        comic_from_cbz = parse_tag_v2(comic_from_cbz)
    else:
        cprint.debug(f"Using v1 parser for {file_path}")
        comic_from_cbz = parse_tag_v1(comic_from_cbz)

    if not comic_from_cbz:
        return cprint.error(f"Failed to parse {file_path}")

    comic_from_cbz.series = clean_manga_title(comic_from_cbz.series)
    comic_from_cbz.notes = "parsed"
    comic_from_cbz.age_rating = "Adults Only 18+"
    if comic_from_cbz.writer == "" and comic_from_cbz.penciller != "":
        comic_from_cbz.writer = comic_from_cbz.penciller
    elif comic_from_cbz.writer != "" and comic_from_cbz.penciller == "":
        comic_from_cbz.penciller = comic_from_cbz.writer

    apply_fixes(comic_from_cbz)

    if SIMULATE:
        cprint.info(f"Simulating {file_path}")
        return

    # cbz_content = comic_from_cbz.pack()
    # (output_path if output_path is not None else file_path).write_bytes(cbz_content)
    path_to_write = (output_path / file_path.name) if output_path else file_path
    if path_to_write != file_path:
        shutil.copy2(file_path, path_to_write)
    comic_from_cbz.save(path_to_write)
    cprint.success(f"Parsed {file_path}")


def parser_callback(
    file_path: Path,
    output_path: Path | None = None,
) -> None:
    if threshold_counter.is_threshold_reached():
        raise SkipThresholdReached

    try:
        cprint.info(f"Parsing {file_path}")
        parse_cbz(file_path, output_path)
    except Exception as e:
        cprint.error(f"Error parsing {file_path} [{e.__class__.__name__}]: {e}")


def main():
    input_path = output_path = None
    if len(sys.argv) >= 3:
        input_path = Path(sys.argv[1])
        output_path = Path(sys.argv[2])
    elif len(sys.argv) == 2:
        input_path = Path(sys.argv[1])
    else:
        input_path = Path(".")

    if input_path.is_file():
        parser_callback(input_path, input_path.parent)
        return

    cprint.warning("Building list of files...")
    list_dir = sorted(
        input_path.rglob("*.cbz"), key=lambda x: x.stat().st_mtime, reverse=True
    )

    for path in list_dir:
        try:
            parser_callback(path, output_path)
        except SkipThresholdReached:
            cprint.warning("Threshold reached. Exiting...")
            break


if __name__ == "__main__":
    main()
