from datetime import date

import pytest

from app.urls import RejectReason, TrapTracker, UrlGate, UrlEvaluator, UrlPolicy, detect_stateless_trap, normalize_url
from app.urls.traps import calendar_info, has_repeating_cycle, pagination_info, pattern_signature

TODAY = date(2026, 9, 30)
P = UrlPolicy()


def n(url, policy=P):
    return normalize_url("https://example.com" + url if url.startswith("/") else url, policy=policy)


def trap(url, policy=P):
    result = detect_stateless_trap(n(url, policy), policy, TODAY)
    return result[0] if result else None


class TestDepthAndRepetition:
    def test_depth_limit(self):
        assert trap("/" + "/".join(f"s{i}" for i in range(12))) is None
        assert trap("/" + "/".join(f"s{i}" for i in range(13))) == RejectReason.TRAP_DEPTH

    def test_depth_limit_configurable(self):
        assert trap("/a/b/c", UrlPolicy(max_path_depth=2)) == RejectReason.TRAP_DEPTH

    @pytest.mark.parametrize("path", ["/a/a/a", "/x/foo/foo/foo/bar", "/a/b/a/b/a/b", "/a/b/c/a/b/c/a/b/c", "/team/team/team/team"])
    def test_repeating_cycles_are_traps(self, path):
        assert trap(path) == RejectReason.TRAP_REPEATED_SEGMENTS

    @pytest.mark.parametrize("path", ["/a/a", "/a/b/a/b", "/a/b/a/c/a/b", "/about/team/about", "/x/x/y/x/x/y"])
    def test_legitimate_repetition_allowed(self, path):
        assert trap(path) is None

    def test_cycle_function(self):
        assert has_repeating_cycle(["a", "a", "a"], 3)
        assert not has_repeating_cycle(["a", "a"], 3)
        assert has_repeating_cycle(["x", "a", "a", "b"], 2)
        assert not has_repeating_cycle([], 3)

    def test_relative_link_loop_is_caught(self):
        # A page at /a/ linking to "a/" produces /a/a, /a/a/a, ... forever.
        gate = UrlGate(UrlEvaluator(seed_url="https://example.com"))
        url = "https://example.com/a"
        results = []
        for _ in range(6):
            d = gate.admit("a/", base=url + "/")
            results.append(d.allowed)
            url = url + "/a"
        assert results[0] is True  # /a/a is fine
        assert not any(results[1:])  # /a/a/a and deeper are cut off


class TestQueryParams:
    def test_param_count_limit(self):
        ok = "/p?" + "&".join(f"k{i}=1" for i in range(12))
        bad = "/p?" + "&".join(f"k{i}=1" for i in range(13))
        assert trap(ok) is None
        assert trap(bad) == RejectReason.TRAP_QUERY_PARAMS


class TestPagination:
    @pytest.mark.parametrize("url,num", [
        ("/list?page=3", 3), ("/list?PAGE=3", 3), ("/list?paged=2", 2), ("/list?pg=9", 9), ("/list?pagenum=4", 4),
        ("/blog/page/7", 7), ("/blog/page/7/", 7), ("/blog/page-7", 7), ("/blog/page7", 7), ("/blog/Page/7", 7),
    ])
    def test_detection(self, url, num):
        assert pagination_info(n(url)).number == num

    @pytest.mark.parametrize("url", ["/list", "/list?p=5", "/list?page=abc", "/list?page=", "/page/about", "/pages/3", "/list?start=20240101"])
    def test_non_pagination(self, url):
        assert pagination_info(n(url)) is None

    def test_page_limit(self):
        assert trap("/list?page=50") is None
        assert trap("/list?page=51") == RejectReason.TRAP_PAGINATION
        assert trap("/blog/page/51") == RejectReason.TRAP_PAGINATION
        assert trap("/blog/page-999") == RejectReason.TRAP_PAGINATION

    def test_huge_page_numbers_do_not_overflow(self):
        assert trap("/list?page=" + "9" * 40) == RejectReason.TRAP_PAGINATION
        trap("/blog/page" + "9" * 40)  # over-long segment must not crash

    def test_page_limit_configurable(self):
        assert trap("/list?page=6", UrlPolicy(max_page_number=5)) == RejectReason.TRAP_PAGINATION

    def test_offset_limit(self):
        assert trap("/list?offset=2500") is None
        assert trap("/list?offset=2501") == RejectReason.TRAP_PAGINATION
        assert trap("/list?skip=99999") == RejectReason.TRAP_PAGINATION

    def test_base_key_ignores_page(self):
        a, b = pagination_info(n("/list?page=1&cat=x")), pagination_info(n("/list?cat=x&page=2"))
        assert a.base_key == b.base_key
        assert pagination_info(n("/list?page=1&cat=y")).base_key != a.base_key
        assert pagination_info(n("/blog/page/2")).base_key == pagination_info(n("/blog/page/3")).base_key

    def test_empty_page_cutoff(self):
        t = TrapTracker(P)
        assert t.admit(n("/list?page=3")) is None
        t.record_empty_page(n("/list?page=4"))
        assert t.admit(n("/list?page=3&x=1")) is None  # different listing (extra filter)
        assert t.admit(n("/list?page=4"))[0] == RejectReason.TRAP_PAGINATION
        assert t.admit(n("/list?page=9"))[0] == RejectReason.TRAP_PAGINATION
        assert t.admit(n("/list?page=2")) is None
        assert t.admit(n("/other?page=9")) is None

    def test_empty_page_cutoff_keeps_lowest(self):
        t = TrapTracker(P)
        t.record_empty_page(n("/blog/page/8"))
        t.record_empty_page(n("/blog/page/5"))
        t.record_empty_page(n("/blog/page/9"))
        assert t.admit(n("/blog/page/4")) is None
        assert t.admit(n("/blog/page/5"))[0] == RejectReason.TRAP_PAGINATION

    def test_record_empty_page_ignores_non_paginated(self):
        t = TrapTracker(P)
        t.record_empty_page(n("/team"))  # must not raise
        assert t.admit(n("/team")) is None


class TestCalendar:
    @pytest.mark.parametrize("url", [
        "/events/2026/05", "/events/2026/05/12", "/archive/2026-05", "/2026/09", "/2026-09-30", "/calendar/2026/1",
        "/x?date=2026-05-12", "/x?year=2026&month=5", "/x?startDate=2026-05-01", "/x?ym=202605", "/x?tribe-bar-date=2026-05-01",
        "/calendar", "/x?month=5", "/x?week=3", "/archive/2026",
    ])
    def test_calendar_like(self, url):
        assert calendar_info(n(url)) is not None

    @pytest.mark.parametrize("url", [
        "/blog/2026/05/my-post-title", "/team", "/product/1999", "/team/2001", "/x?id=2026", "/x?page=2026", "/orders/2026/13",
    ])
    def test_not_calendar_like(self, url):
        assert calendar_info(n(url)) is None

    @pytest.mark.parametrize("url", ["/events/2035/05", "/events/2010/05/12", "/x?date=2040-01-01", "/x?year=1999", "/archive/2001", "/cal?startDate=2030-01-01"])
    def test_out_of_range_years_are_traps(self, url):
        assert trap(url) == RejectReason.TRAP_CALENDAR

    @pytest.mark.parametrize("url", ["/events/2026/05", "/events/2027/01", "/events/2021/12", "/x?date=2026-09-30"])
    def test_in_range_years_allowed(self, url):
        assert trap(url) is None

    def test_window_edges_and_config(self):
        assert trap("/events/2021/05") is None   # 2026 - 5
        assert trap("/events/2020/05") == RejectReason.TRAP_CALENDAR
        assert trap("/events/2027/05") is None   # 2026 + 1
        assert trap("/events/2028/05") == RejectReason.TRAP_CALENDAR
        wide = UrlPolicy(calendar_years_back=10, calendar_years_ahead=3)
        assert trap("/events/2018/05", wide) is None
        assert trap("/events/2029/05", wide) is None

    @pytest.mark.parametrize("value,year", [("2026", 2026), ("2026-05-01", 2026), ("202605", 2026), ("20260501", 2026), ("01/05/2026", 2026)])
    def test_date_values_parse(self, value, year):
        assert calendar_info(n(f"/x?date={value}")).years == (year,)

    @pytest.mark.parametrize("value", ["20265", "2026123", "abc", "", "1850-01-01"])
    def test_non_date_values_ignored(self, value):
        info = calendar_info(n(f"/x?date={value}"))
        assert info is None or info.years == ()

    def test_blog_post_urls_are_not_treated_as_calendars(self):
        assert trap("/blog/2012/05/hello-world") is None

    def test_clock_is_injectable(self):
        p = normalize_url("https://example.com/events/2010/05", policy=P)
        assert detect_stateless_trap(p, P, date(2011, 1, 1)) is None
        assert detect_stateless_trap(p, P, date(2026, 1, 1))[0] == RejectReason.TRAP_CALENDAR

    def test_signature_masks_numbers(self):
        assert pattern_signature(n("/events/2026/05")) == pattern_signature(n("/events/2026/06"))
        assert pattern_signature(n("/x?date=2026-05-01")) == pattern_signature(n("/x?date=2026-05-02"))
        assert pattern_signature(n("/events/2026/05")) != pattern_signature(n("/news/2026/05"))

    def test_next_month_loop_is_capped_in_range(self):
        t = TrapTracker(UrlPolicy(max_calendar_urls_per_pattern=5, max_query_variants_per_path=1000))
        admitted = 0
        for month in range(1, 13):
            if t.admit(n(f"/events?view=month&date=2026-{month:02d}-01")) is None:
                admitted += 1
        assert admitted == 5

    def test_calendar_cap_is_per_pattern(self):
        t = TrapTracker(UrlPolicy(max_calendar_urls_per_pattern=2))
        assert t.admit(n("/events/2026/01")) is None
        assert t.admit(n("/events/2026/02")) is None
        assert t.admit(n("/events/2026/03"))[0] == RejectReason.TRAP_CALENDAR
        assert t.admit(n("/news/2026/03")) is None  # a different pattern
        assert t.admit(n("/events/2026/02")) is None  # already counted, not new

    def test_calendar_cap_applies_to_path_style_urls_via_gate(self):
        ev = UrlEvaluator(UrlPolicy(max_calendar_urls_per_pattern=3), "https://example.com", clock=lambda: TODAY)
        gate = UrlGate(ev)
        results = [gate.admit(f"/archive/2026/{m:02d}", base="https://example.com/").allowed for m in range(1, 7)]
        assert results == [True, True, True, False, False, False]
        assert gate.rejections[RejectReason.TRAP_CALENDAR] == 3


class TestQueryVariants:
    def test_variant_cap_per_path(self):
        t = TrapTracker(UrlPolicy(max_query_variants_per_path=3))
        results = [t.admit(n(f"/search?q=term{i}")) is None for i in range(6)]
        assert results == [True, True, True, False, False, False]
        assert t.admit(n("/search?q=term1")) is None  # existing variant is fine
        assert t.admit(n("/other?q=new")) is None  # other paths are independent

    def test_rejection_reason(self):
        t = TrapTracker(UrlPolicy(max_query_variants_per_path=1))
        t.admit(n("/s?a=1"))
        assert t.admit(n("/s?a=2"))[0] == RejectReason.TRAP_QUERY_VARIANTS

    def test_page_param_is_not_a_variant(self):
        t = TrapTracker(UrlPolicy(max_query_variants_per_path=2))
        for page in range(1, 11):
            assert t.admit(n(f"/list?page={page}")) is None
        assert t.admit(n("/list?page=1&sort=asc")) is None
        assert t.admit(n("/list?page=2&sort=desc")) is None
        assert t.admit(n("/list?page=3&sort=name"))[0] == RejectReason.TRAP_QUERY_VARIANTS

    def test_no_query_never_counts(self):
        t = TrapTracker(UrlPolicy(max_query_variants_per_path=1))
        for _ in range(5):
            assert t.admit(n("/team")) is None

    def test_check_does_not_mutate(self):
        t = TrapTracker(UrlPolicy(max_query_variants_per_path=1))
        for i in range(5):
            assert t.check(n(f"/s?a={i}")) is None
        assert t.admit(n("/s?a=1")) is None
        assert t.check(n("/s?a=2")) is not None
