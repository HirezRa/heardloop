"""Reading post ids from LinkedIn addresses and export files (FR-29). Pure functions, no network."""

import pytest

from heardloop.analytics_import import parse_export
from heardloop.linkedin.post_link import (
    PostLinkError,
    activity_id_from_export_name,
    kind_of_url,
    parse_post_link,
)
from tests.xlsx_builder import layout_a, layout_b

ACTIVITY = "7519065369198620675"
UGC = "7518638656480395265"


@pytest.mark.parametrize(
    ("link", "kind", "post_id"),
    [
        (f"https://www.linkedin.com/feed/update/urn:li:activity:{ACTIVITY}/", "activity", ACTIVITY),
        (f"https://www.linkedin.com/feed/update/urn%3Ali%3Aactivity%3A{ACTIVITY}", "activity", ACTIVITY),
        (f"www.linkedin.com/feed/update/urn:li:share:{UGC}/?utm_source=x", "share", UGC),
        (f"https://linkedin.com/feed/update/urn:li:ugcPost:{UGC}", "ugcPost", UGC),
        (
            f"https://www.linkedin.com/posts/someone-tech_opensource-selfhosted-ugcPost-{UGC}-ls-K",
            "ugcPost",
            UGC,
        ),
        (f"https://www.linkedin.com/posts/someone_topic-activity-{ACTIVITY}-AbCd/", "activity", ACTIVITY),
        (f"  https://m.linkedin.com/feed/update/urn:li:activity:{ACTIVITY}  ", "activity", ACTIVITY),
    ],
)
def test_post_links_are_read_offline(link: str, kind: str, post_id: str) -> None:
    parsed = parse_post_link(link)
    assert (parsed.kind, parsed.id) == (kind, post_id)
    assert parsed.urn == f"urn:li:{kind}:{post_id}"


@pytest.mark.parametrize(
    ("link", "code"),
    [
        ("https://lnkd.in/p/d9-544yc", "short_link"),
        ("lnkd.in/abc", "short_link"),
        (f"https://example.com/feed/update/urn:li:activity:{ACTIVITY}", "not_linkedin"),
        (f"https://linkedin.com.evil.example/feed/update/urn:li:activity:{ACTIVITY}", "not_linkedin"),
        (f"ftp://www.linkedin.com/feed/update/urn:li:activity:{ACTIVITY}", "not_linkedin"),
        ("https://www.linkedin.com/in/someone/", "invalid_post_link"),
        ("https://www.linkedin.com/feed/update/urn:li:activity:123/", "invalid_post_link"),
        ("", "invalid_post_link"),
        ("https://www.linkedin.com/feed/update/urn:li:activity:" + "1" * 30, "invalid_post_link"),
        ("https://www.linkedin.com/\nfeed", "invalid_post_link"),
        ("https://www.linkedin.com/" + "a" * 3000, "invalid_post_link"),
    ],
)
def test_other_addresses_are_refused(link: str, code: str) -> None:
    with pytest.raises(PostLinkError) as err:
        parse_post_link(link)
    assert err.value.code == code


def test_activity_id_from_the_export_file_name() -> None:
    assert activity_id_from_export_name(f"SinglePostAnalytics_Some Person_{ACTIVITY}.xlsx") == ACTIVITY
    assert activity_id_from_export_name(f"{ACTIVITY}.XLSX") == ACTIVITY
    assert activity_id_from_export_name("export.xlsx") is None
    assert activity_id_from_export_name("SinglePostAnalytics_Some Person_123.xlsx") is None


def test_export_reports_which_id_it_carries() -> None:
    assert kind_of_url(f"https://www.linkedin.com/feed/update/urn:li:share:{UGC}") == "share"
    assert kind_of_url(f"https://www.linkedin.com/posts/x_y-ugcPost-{UGC}-ls-K") == "ugcPost"
    assert kind_of_url("https://www.linkedin.com/posts/unknown") == "share"
    assert parse_export(layout_a(UGC)).post_kind == "share"
    parsed = parse_export(layout_b(UGC, vanity="ugcPost"))
    assert (parsed.share_id, parsed.post_kind) == (UGC, "ugcPost")
    assert parse_export(layout_b(ACTIVITY, vanity="activity")).post_kind == "activity"
