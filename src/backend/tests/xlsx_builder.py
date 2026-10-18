"""Builds minimal valid .xlsx workbooks in memory for the analytics import tests (no binary fixtures)."""

import io
import zipfile
from xml.sax.saxutils import escape

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XML = '<?xml version="1.0" encoding="UTF-8"?>'


def _col(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def make_xlsx(sheets: dict[str, list[list[str]]], *, extra: dict[str, bytes] | None = None) -> bytes:
    """`sheets`: sheet name -> rows of strings. Strings go through sharedStrings like a real export."""
    shared: list[str] = []
    index: dict[str, int] = {}

    def sid(text: str) -> int:
        if text not in index:
            index[text] = len(shared)
            shared.append(text)
        return index[text]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        sheet_tags, rel_tags = [], []
        for n, (name, rows) in enumerate(sheets.items(), start=1):
            body = []
            for r, row in enumerate(rows, start=1):
                cells = "".join(
                    f'<c r="{_col(c)}{r}" t="s"><v>{sid(v)}</v></c>' for c, v in enumerate(row) if v != ""
                )
                body.append(f'<row r="{r}">{cells}</row>')
            zf.writestr(
                f"xl/worksheets/sheet{n}.xml",
                _XML + f'<worksheet xmlns="{_MAIN}"><sheetData>{"".join(body)}</sheetData></worksheet>',
            )
            sheet_tags.append(f'<sheet name="{escape(name)}" sheetId="{n}" r:id="rId{n}"/>')
            rel_tags.append(
                f'<Relationship Id="rId{n}" Type="{_REL}/worksheet" Target="worksheets/sheet{n}.xml"/>'
            )
        zf.writestr(
            "xl/workbook.xml",
            _XML
            + f'<workbook xmlns="{_MAIN}" xmlns:r="{_REL}"><sheets>{"".join(sheet_tags)}</sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            _XML
            + f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{"".join(rel_tags)}</Relationships>',
        )
        strings = "".join(f"<si><t>{escape(s)}</t></si>" for s in shared)
        overrides = extra or {}
        if "xl/sharedStrings.xml" not in overrides:
            count = len(shared)
            zf.writestr(
                "xl/sharedStrings.xml",
                _XML + f'<sst xmlns="{_MAIN}" count="{count}" uniqueCount="{count}">{strings}</sst>',
            )
        for name, data in overrides.items():
            zf.writestr(name, data)
    return buf.getvalue()


def layout_a(
    share_id: str = "7114500000000000001",
    *,
    date: str = "Sep 9, 2026",
    time: str = "5:30 AM",
    exported: str = "Oct 17, 2026",
    impressions: str = "1,200",
) -> bytes:
    """Layout A: PERFORMANCE + TOP DEMOGRAPHICS, publish time in UTC, demographics as floats."""
    performance = [
        ["Post URL", f"https://www.linkedin.com/feed/update/urn:li:share:{share_id}"],
        ["Post Date", date],
        ["Post Publish Time", time],
        ["Impressions", impressions],
        ["Members reached", "800"],
        ["Profile viewers from this post", "12"],
        ["Followers gained from this post", "3"],
        ["Reactions", "40"],
        ["Comments", "5"],
        ["Reposts", "2"],
        ["Saves", "7"],
        ["Sends on LinkedIn", "4"],
        [f"Reactions Highlights Sep 9, 2026 to {exported}"],
        ["Top job title", "Software Engineer"],
        ["Top location", "Tel Aviv"],
        ["Top industry", "Software Development"],
    ]
    demographics = [
        ["Category", "Value", "%"],
        ["Seniority", "Senior", "0.365"],
        ["Seniority", "Entry", "0.1"],
        ["Industry", "IT Services", "0.25"],
    ]
    return make_xlsx({"PERFORMANCE": performance, "TOP DEMOGRAPHICS": demographics})


def layout_b(
    share_id: str = "7114500000000000002",
    *,
    vanity: str = "share",
    date: str = "9/9/2026",
    time: str = "8:30 AM",
) -> bytes:
    """Layout B: one sheet `Post analytics`, local publish time, demographics as "36%" strings."""
    rows = [
        ["Post URL", f"https://www.linkedin.com/posts/some-slug-{vanity}-{share_id}-AbCd"],
        ["Post Date", date],
        ["Post Publish Time", time],
        ["Discovery"],
        ["Impressions", "2,500"],
        ["Members reached", "1,900"],
        ["Profile activity"],
        ["Profile viewers from this post", "30"],
        ["Followers gained from this post", "6"],
        ["Engagement"],
        ["Reactions", "90"],
        ["Comments", "11"],
        ["Reposts", "4"],
        ["Saves", "9"],
        ["Sends on LinkedIn", "8"],
        ["Social engagements", "114"],
        ["Link engagements", "21"],
        ["Premium custom button engagements", "0"],
        ["Post viewer demographics"],
        ["Category", "Value", "%"],
        ["Job titles", "Founder", "36%"],
        ["Locations", "Greater Tel Aviv", "20%"],
    ]
    return make_xlsx({"Post analytics": rows})
