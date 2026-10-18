"""LinkedIn analytics export parser: both layouts and the zip guards. Workbooks are built in memory."""

import io
import zipfile
from datetime import UTC, date, datetime

import pytest

from heardloop import analytics_import
from heardloop.analytics_import import ExportParseError, parse_export
from heardloop.formdata import FormDataError, parse_multipart
from tests.xlsx_builder import layout_a, layout_b, make_xlsx


def test_layout_a_full_export() -> None:
    parsed = parse_export(layout_a())
    assert parsed.layout == "A"
    assert parsed.share_id == "7114500000000000001"
    assert parsed.post_url.endswith("urn:li:share:7114500000000000001")
    assert parsed.published_at_hint == datetime(2026, 9, 9, 5, 30, tzinfo=UTC)  # UTC as stated
    assert parsed.captured_on == date(2026, 10, 17)
    assert parsed.metrics == {
        "impressions": 1200,
        "members_reached": 800,
        "profile_viewers": 12,
        "followers_gained": 3,
        "reactions": 40,
        "comments": 5,
        "reposts": 2,
        "saves": 7,
        "sends": 4,
        "link_clicks": None,
    }
    assert parsed.top == {
        "job_title": "Software Engineer",
        "location": "Tel Aviv",
        "industry": "Software Development",
    }
    assert [(d.category, d.value, d.share) for d in parsed.demographics] == [
        ("Seniority", "Senior", 0.365),
        ("Seniority", "Entry", 0.1),
        ("Industry", "IT Services", 0.25),
    ]


def test_layout_b_full_export() -> None:
    parsed = parse_export(layout_b())
    assert parsed.layout == "B"
    assert parsed.share_id == "7114500000000000002"
    assert parsed.published_at_hint == datetime(2026, 9, 9, 8, 30)  # noqa: DTZ001  # naive: caller localises
    assert parsed.published_at_hint is not None and parsed.published_at_hint.tzinfo is None
    assert parsed.captured_on is None
    assert parsed.metrics["impressions"] == 2500
    assert parsed.metrics["members_reached"] == 1900
    assert parsed.metrics["link_clicks"] == 21  # "Link engagements"
    assert parsed.metrics["sends"] == 8
    assert parsed.top == {}
    assert [(d.category, d.value, d.share) for d in parsed.demographics] == [
        ("Job titles", "Founder", 0.36),
        ("Locations", "Greater Tel Aviv", 0.2),
    ]


def test_layout_b_accepts_activity_id_in_vanity_url() -> None:
    parsed = parse_export(layout_b("7114500000000000003", vanity="activity"))
    assert parsed.share_id == "7114500000000000003"


def test_missing_metrics_stay_none_and_text_is_cleaned() -> None:
    rows = [
        ["Post URL", "https://www.linkedin.com/feed/update/urn:li:share:7114500000000000009"],
        ["Impressions", "10"],
        ["Reactions", "n/a"],
        ["Top location", "Haifa\x85\x9f \t Israel"],
    ]
    parsed = parse_export(make_xlsx({"PERFORMANCE": rows}))
    assert parsed.metrics["impressions"] == 10 and parsed.metrics["reactions"] is None
    assert parsed.published_at_hint is None and parsed.demographics == []
    assert parsed.top == {"location": "Haifa Israel"}


def test_demographics_are_capped_and_trimmed() -> None:
    demo = [["Category", "Value", "%"]] + [[f"Cat{i}", "v" * 300, "0.01"] for i in range(80)]
    perf = [["Post URL", "https://www.linkedin.com/feed/update/urn:li:share:7114500000000000010"]]
    parsed = parse_export(make_xlsx({"PERFORMANCE": perf, "TOP DEMOGRAPHICS": demo}))
    assert len(parsed.demographics) == analytics_import.MAX_DEMOGRAPHICS
    assert len(parsed.demographics[0].value) == analytics_import.MAX_TEXT


def test_unrecognised_layout_and_missing_share_id() -> None:
    with pytest.raises(ExportParseError) as unknown:
        parse_export(make_xlsx({"Sheet1": [["a", "b"]]}))
    assert unknown.value.code == "unrecognised_layout"
    with pytest.raises(ExportParseError) as no_id:
        parse_export(make_xlsx({"PERFORMANCE": [["Post URL", "https://www.linkedin.com/in/someone"]]}))
    assert no_id.value.code == "no_share_id"
    with pytest.raises(ExportParseError) as no_url:
        parse_export(make_xlsx({"Post analytics": [["Impressions", "5"]]}))
    assert no_url.value.code == "no_share_id"


@pytest.mark.parametrize("data", [b"", b"not a zip at all", b"PK\x03\x04" + b"\x00" * 40])
def test_not_xlsx(data: bytes) -> None:
    with pytest.raises(ExportParseError) as err:
        parse_export(data)
    assert err.value.code == "not_xlsx"


def test_zip_without_workbook_is_not_xlsx() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(ExportParseError) as err:
        parse_export(buf.getvalue())
    assert err.value.code == "not_xlsx"


def test_xml_with_entity_declarations_is_refused() -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><sst xmlns="x">&a;</sst>'
    data = make_xlsx({"PERFORMANCE": [["Post URL", "x"]]}, extra={"xl/sharedStrings.xml": bomb})
    with pytest.raises(ExportParseError) as err:
        parse_export(data)
    assert err.value.code == "not_xlsx"


def test_guard_file_larger_than_two_megabytes() -> None:
    with pytest.raises(ExportParseError) as err:
        parse_export(b"\x00" * (analytics_import.MAX_FILE_BYTES + 1))
    assert err.value.code == "too_large"


def test_guard_too_many_members() -> None:
    extra = {f"xl/media/f{i}.txt": b"x" for i in range(analytics_import.MAX_MEMBERS)}
    with pytest.raises(ExportParseError) as err:
        parse_export(make_xlsx({"PERFORMANCE": [["a"]]}, extra=extra))
    assert err.value.code == "too_large"


def test_guard_member_larger_than_five_megabytes() -> None:
    big = b"A" * (analytics_import.MAX_MEMBER_BYTES + 1)
    data = make_xlsx({"PERFORMANCE": [["a"]]}, extra={"xl/media/big.bin": big})
    assert len(data) < analytics_import.MAX_FILE_BYTES  # tiny on the wire, huge when inflated
    with pytest.raises(ExportParseError) as err:
        parse_export(data)
    assert err.value.code == "too_large"


def test_guard_compression_ratio() -> None:
    # 1 MB of zeros deflates to about 1 KB: ratio far above 100 although the member is under 5 MB.
    data = make_xlsx({"PERFORMANCE": [["a"]]}, extra={"xl/media/zeros.bin": b"\x00" * 1_000_000})
    with pytest.raises(ExportParseError) as err:
        parse_export(data)
    assert err.value.code == "too_large"


def test_multipart_reader() -> None:
    payload = b"PK\x03\x04\r\n--not-the-boundary\r\n\x00\xff"
    body = (
        b'--XyZ\r\nContent-Disposition: form-data; name="file"; filename="a.xlsx"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n" + payload + b"\r\n"
        b'--XyZ\r\nContent-Disposition: form-data; name="create_missing"\r\n\r\ntrue\r\n--XyZ--\r\n'
    )
    fields = parse_multipart("multipart/form-data; boundary=XyZ", body)
    assert fields == {"file": payload, "create_missing": b"true"}
    with pytest.raises(FormDataError):
        parse_multipart("application/json", body)
    with pytest.raises(FormDataError):
        parse_multipart("multipart/form-data", body)
    with pytest.raises(FormDataError):
        parse_multipart("multipart/form-data; boundary=XyZ", body.replace(b"--XyZ--", b""))
    with pytest.raises(FormDataError):  # the same field twice
        parse_multipart("multipart/form-data; boundary=XyZ", body.replace(b"create_missing", b"file"))
