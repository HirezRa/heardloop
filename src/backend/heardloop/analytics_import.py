"""Reader for the "Post analytics" workbooks LinkedIn lets you export from a post (.xlsx).

Pure functions, standard library only (zipfile + xml.etree). The file is never written anywhere; only the
numbers and a few aggregated audience fields are returned. Two layouts exist:

* Layout A - sheets `PERFORMANCE` and `TOP DEMOGRAPHICS`; the publish time is UTC, demographics are floats
  between 0 and 1.
* Layout B - one sheet `Post analytics`; the publish time is the viewer's local time (returned naive, the
  caller localises it), demographics are strings such as `36%`.

Everything is validated defensively: the workbook comes from a user's disk and is treated as hostile input.
"""

import io
import re
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from xml.etree import (
    ElementTree as ET,  # DOCTYPE/ENTITY declarations are refused before parsing
)
from xml.parsers import expat

from heardloop.linkedin.post_link import kind_of_url

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_MEMBERS = 50
MAX_MEMBER_BYTES = 5 * 1024 * 1024
MAX_RATIO = 100  # uncompressed / compressed; real workbooks are far below this
RATIO_MIN_SIZE = 10 * 1024  # tiny members may compress extremely well without being a threat
MAX_ROWS = 5000
MAX_COLUMNS = 16384  # Excel's last column, XFD
MAX_CELLS = 200_000  # per sheet, counted as the expanded row widths
MAX_DEMOGRAPHICS = 50
MAX_TEXT = 120
MAX_METRIC = 10**12

METRIC_KEYS = (
    "impressions",
    "members_reached",
    "profile_viewers",
    "followers_gained",
    "reactions",
    "comments",
    "reposts",
    "saves",
    "sends",
    "link_clicks",
)

_LABELS = {
    "impressions": "impressions",
    "members reached": "members_reached",
    "profile viewers from this post": "profile_viewers",
    "followers gained from this post": "followers_gained",
    "reactions": "reactions",
    "comments": "comments",
    "reposts": "reposts",
    "saves": "saves",
    "sends on linkedin": "sends",
    "link engagements": "link_clicks",
}
_TOP_LABELS = {"top job title": "job_title", "top location": "location", "top industry": "industry"}

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_URN_ID = re.compile(r"urn:li:(?:share|ugcPost|activity):(\d{10,25})")
_VANITY_ID = re.compile(r"(?:share|ugcPost|activity)-(\d{10,25})")
_COLUMN = re.compile(r"^([A-Z]+)")


class ExportParseError(Exception):
    """The workbook cannot be used. `code`: not_xlsx, too_large, unrecognised_layout or no_share_id."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Demographic:
    category: str
    value: str
    share: float  # 0-1


@dataclass(frozen=True)
class ParsedExport:
    layout: str  # "A" or "B"
    share_id: str
    post_url: str
    published_at_hint: datetime | None  # layout A: aware UTC; layout B: naive, the caller localises it
    captured_on: date | None  # layout A only (the export day); None for layout B
    metrics: dict[str, int | None]
    demographics: list[Demographic] = field(default_factory=list)
    top: dict[str, str | None] = field(default_factory=dict)
    # Which LinkedIn id `share_id` is: "share", "ugcPost" or "activity" (as written in the Post URL).
    post_kind: str = "share"


# ---- zip and XML plumbing ----


def _clean(value: str) -> str:
    return " ".join(_CONTROL.sub(" ", value).split())[:MAX_TEXT]


def _read_member(zf: zipfile.ZipFile, name: str, *, required: bool = False) -> bytes | None:
    try:
        info = zf.getinfo(name)
    except KeyError:
        if required:
            raise ExportParseError("not_xlsx") from None
        return None
    try:
        with zf.open(info) as handle:
            data = handle.read(MAX_MEMBER_BYTES + 1)  # the header size can lie; bound what is really inflated
    except (RuntimeError, zipfile.BadZipFile, zlib.error, EOFError, OSError, NotImplementedError):
        raise ExportParseError("not_xlsx") from None  # corrupt, truncated or password-protected
    if len(data) > MAX_MEMBER_BYTES:
        raise ExportParseError("too_large")
    _refuse_dtd(data)
    return data


class _DtdFound(Exception):
    pass


def _dtd_handler(*_args: object) -> None:
    raise _DtdFound


def _refuse_dtd(data: bytes) -> None:
    """Refuses any DOCTYPE or entity declaration, whatever the document encoding.

    A byte search for `<!DOCTYPE` misses UTF-16 and other encodings, so expat itself reports the
    declaration (it decodes the document first). Real xlsx members never contain one.
    """
    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = _dtd_handler
    parser.EntityDeclHandler = _dtd_handler
    try:
        parser.Parse(data, True)
    except (_DtdFound, expat.ExpatError, ValueError):
        raise ExportParseError("not_xlsx") from None


def _xml(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data)  # noqa: S314  # DTDs are refused by _refuse_dtd in _read_member
    except ET.ParseError:
        raise ExportParseError("not_xlsx") from None


def _open_workbook(data: bytes) -> zipfile.ZipFile:
    if len(data) > MAX_FILE_BYTES:
        raise ExportParseError("too_large")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
        raise ExportParseError("not_xlsx") from None
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise ExportParseError("too_large")
    for info in infos:
        if info.file_size > MAX_MEMBER_BYTES:
            raise ExportParseError("too_large")
        if info.file_size >= RATIO_MIN_SIZE and (
            info.compress_size == 0 or info.file_size / info.compress_size > MAX_RATIO
        ):
            raise ExportParseError("too_large")
    if "xl/workbook.xml" not in zf.namelist():
        raise ExportParseError("not_xlsx")
    return zf


def _text_of(element: ET.Element) -> str:
    """Concatenated `<t>` text of a shared string or inline string, skipping phonetic runs."""
    parts: list[str] = []
    for node in element.iter():
        if node.tag == f"{{{_NS['m']}}}t" and node.text:
            parts.append(node.text)
    return "".join(parts)


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    data = _read_member(zf, "xl/sharedStrings.xml")
    if data is None:
        return []
    root = _xml(data)
    out: list[str] = []
    for si in root.findall("m:si", _NS):
        # <rPh> (phonetic hints) also contains <t>; the visible text is in <t> directly under <si> or <r>.
        visible = [n for n in si if n.tag != f"{{{_NS['m']}}}rPh"]
        holder = ET.Element(si.tag)
        holder.extend(visible)
        out.append(_text_of(holder))
    return out


def _plain_number(raw: str) -> str:
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return raw
    if not number.is_finite():
        return raw
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def _column_index(ref: str) -> int:
    match = _COLUMN.match(ref)
    if not match:
        return -1
    letters = match.group(1)
    if len(letters) > 3:
        raise ExportParseError("too_large")  # beyond Excel's last column XFD
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    if index > MAX_COLUMNS:
        raise ExportParseError("too_large")
    return index - 1


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        inline = cell.find("m:is", _NS)
        return _text_of(inline) if inline is not None else ""
    v = cell.find("m:v", _NS)
    raw = v.text if v is not None and v.text is not None else ""
    if kind == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if kind in ("str", "e"):
        return raw
    if kind == "b":
        return "1" if raw.strip() == "1" else "0"
    return _plain_number(raw.strip()) if raw.strip() else ""


def _sheet_rows(root: ET.Element, shared: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    total = 0
    for row in root.iter(f"{{{_NS['m']}}}row"):
        if len(rows) >= MAX_ROWS:
            break
        cells: dict[int, str] = {}
        for position, cell in enumerate(row.findall("m:c", _NS)):
            index = _column_index(cell.get("r", ""))
            cells[index if index >= 0 else position] = _cell_value(cell, shared).strip()
        width = max(cells, default=-1) + 1
        total += width
        if total > MAX_CELLS:
            raise ExportParseError("too_large")  # sparse cells far to the right would expand to huge rows
        rows.append([cells.get(i, "") for i in range(width)])
    return rows


def _sheets(zf: zipfile.ZipFile) -> dict[str, list[list[str]]]:
    """Sheet name (lower-case) -> rows of cell strings."""
    workbook = _xml(_read_member(zf, "xl/workbook.xml", required=True) or b"")
    targets: dict[str, str] = {}
    rels = _read_member(zf, "xl/_rels/workbook.xml.rels")
    if rels is not None:
        for rel in _xml(rels).findall("rel:Relationship", _NS):
            rid, target = rel.get("Id"), rel.get("Target")
            if rid and target:
                targets[rid] = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
    shared = _shared_strings(zf)
    fallback = sorted(
        (n for n in zf.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
        key=lambda n: int(re.findall(r"\d+", n)[-1]),
    )
    out: dict[str, list[list[str]]] = {}
    for position, sheet in enumerate(workbook.findall("m:sheets/m:sheet", _NS)):
        name = (sheet.get("name") or "").strip()
        path = targets.get(sheet.get(f"{{{_NS['r']}}}id", ""))
        if path is None and position < len(fallback):
            path = fallback[position]
        data = _read_member(zf, path) if path else None
        if name and data is not None:
            out[name.lower()] = _sheet_rows(_xml(data), shared)
    return out


# ---- value parsing ----


def _count(raw: str) -> int | None:
    text = raw.replace(",", "").replace("\u202f", "").replace(" ", "").strip()
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite() or number < 0 or number > MAX_METRIC:
        return None
    return int(number)


def _share(raw: str) -> float | None:
    text = raw.strip().replace(",", ".")
    percent = text.endswith("%")
    try:
        number = float(text.rstrip("%").strip())
    except ValueError:
        return None
    if percent or number > 1:
        number /= 100
    if not 0 <= number <= 1:
        return None
    return round(number, 4)


_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d")
_TIME_FORMATS = ("%I:%M %p", "%I:%M%p", "%I:%M:%S %p", "%H:%M", "%H:%M:%S")
_EXCEL_EPOCH = datetime(1899, 12, 30)  # noqa: DTZ001  # a serial-number epoch, not a point in time


def _parse_date(raw: str) -> date | None:
    text = raw.strip().replace("\u202f", " ")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    try:  # a date stored as a spreadsheet serial number
        serial = float(text)
    except ValueError:
        return None
    if 20000 < serial < 80000:
        return (_EXCEL_EPOCH + timedelta(days=int(serial))).date()
    return None


def _parse_time(raw: str) -> time | None:
    text = raw.strip().replace("\u202f", " ").upper()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).time()  # noqa: DTZ007
        except ValueError:
            continue
    try:  # a time stored as a fraction of a day
        fraction = float(text)
    except ValueError:
        return None
    if 0 <= fraction < 1:
        seconds = round(fraction * 86400)
        return time(seconds // 3600 % 24, seconds // 60 % 60)
    return None


def _share_id(url: str) -> str | None:
    match = _URN_ID.search(url) or _VANITY_ID.search(url)
    return match.group(1) if match else None


def _pairs(rows: list[list[str]]) -> list[tuple[str, str]]:
    return [(r[0].strip(), r[1].strip() if len(r) > 1 else "") for r in rows if r and r[0].strip()]


def _demographic_rows(rows: list[list[str]]) -> list[Demographic]:
    out: list[Demographic] = []
    for row in rows:
        cells = [c.strip() for c in row]
        if len(cells) < 3 or not cells[0] or not cells[1] or cells[0].lower() == "category":
            continue
        share = _share(cells[2])
        category, value = _clean(cells[0]), _clean(cells[1])
        if share is None or not category or not value:
            continue
        out.append(Demographic(category=category, value=value, share=share))
        if len(out) >= MAX_DEMOGRAPHICS:
            break
    return out


def _highlights_end(label: str) -> date | None:
    match = re.search(r"\bto\s+(.+)$", label.strip(), re.IGNORECASE)
    return _parse_date(match.group(1)) if match else None


def _read_summary(
    rows: list[list[str]],
) -> tuple[dict[str, str], dict[str, int | None], dict[str, str | None], date | None]:
    """Header fields, metrics and top fields of a summary sheet (both layouts share the label vocabulary)."""
    head: dict[str, str] = {}
    metrics: dict[str, int | None] = dict.fromkeys(METRIC_KEYS)
    top: dict[str, str | None] = {}
    captured_on: date | None = None
    for label, value in _pairs(rows):
        key = label.lower()
        if key.startswith("reactions highlights"):
            captured_on = captured_on or _highlights_end(label)
        elif key in ("post url", "post date", "post publish time"):
            head.setdefault(key, value)
        elif key in _LABELS:
            name = _LABELS[key]
            if metrics[name] is None:
                metrics[name] = _count(value)
        elif key in _TOP_LABELS:
            cleaned = _clean(value)
            top.setdefault(_TOP_LABELS[key], cleaned or None)
    return head, metrics, top, captured_on


def _published(head: dict[str, str], *, utc: bool) -> datetime | None:
    day = _parse_date(head.get("post date", ""))
    if day is None:
        return None
    at = _parse_time(head.get("post publish time", "")) or time(0, 0)
    moment = datetime.combine(day, at)
    return moment.replace(tzinfo=UTC) if utc else moment


def _finish(
    layout: str,
    head: dict[str, str],
    metrics: dict[str, int | None],
    top: dict[str, str | None],
    demographics: list[Demographic],
    captured_on: date | None,
) -> ParsedExport:
    url = head.get("post url", "")
    share_id = _share_id(url)
    if share_id is None:
        raise ExportParseError("no_share_id")
    return ParsedExport(
        layout=layout,
        share_id=share_id,
        post_url=url[:2048],
        post_kind=kind_of_url(url),
        published_at_hint=_published(head, utc=layout == "A"),
        captured_on=captured_on if layout == "A" else None,
        metrics=metrics,
        demographics=demographics,
        top={k: v for k, v in top.items() if v},
    )


def parse_export(data: bytes) -> ParsedExport:
    """Reads one exported workbook. Raises `ExportParseError` with a stable code on anything unusable."""
    zf = _open_workbook(data)
    with zf:
        sheets = _sheets(zf)
    if "performance" in sheets:
        head, metrics, top, captured_on = _read_summary(sheets["performance"])
        demographics = _demographic_rows(sheets.get("top demographics", []))
        return _finish("A", head, metrics, top, demographics, captured_on)
    if "post analytics" in sheets:
        rows = sheets["post analytics"]
        head, metrics, top, _ = _read_summary(rows)
        start = next(
            (i for i, r in enumerate(rows) if r and r[0].strip().lower() == "post viewer demographics"), None
        )
        demographics = _demographic_rows(rows[start + 1 :]) if start is not None else []
        return _finish("B", head, metrics, top, demographics, None)
    raise ExportParseError("unrecognised_layout")
