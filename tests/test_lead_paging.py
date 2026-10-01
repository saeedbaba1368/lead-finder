"""Phase 10.4: deterministic Lead sorting and pagination (pure, offline; no scoring or ranking)."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest

from app.leads import (
    BusinessLead, LeadDataError, LeadPagingError, filter_leads, paginate, sort_and_paginate, sort_and_paginate_stored,
    sort_leads,
)
from app.repositories import LeadRepository

UTC = timezone.utc


def at(day: int, hour: int = 0, tz=UTC) -> datetime:
    return datetime(2026, 1, day, hour, tzinfo=tz)


def make(domain, name=None, first=None, last=None, **fields) -> BusinessLead:
    return BusinessLead(website=f"https://{domain}/", business_name=name, first_seen=first, last_seen=last, **fields)


A = make("alpha.example", "Alpha", at(3), at(9))
B = make("bravo.example", "bravo", at(1), at(5))
C = make("charlie.example", "Charlie", at(2), at(7))
D = make("delta.example", "Delta", at(4), at(6))
ALL = [C, A, D, B]


def domains(leads) -> list[str]:
    return [lead.domain for lead in leads]


# ---------------------------------------------------------------- 1. ascending
def test_ascending_sort_for_every_field():
    assert domains(sort_leads(ALL, "domain")) == ["alpha.example", "bravo.example", "charlie.example", "delta.example"]
    assert domains(sort_leads(ALL, "business_name")) == ["alpha.example", "bravo.example", "charlie.example", "delta.example"]
    assert domains(sort_leads(ALL, "first_seen")) == ["bravo.example", "charlie.example", "alpha.example", "delta.example"]
    assert domains(sort_leads(ALL, "last_seen")) == ["bravo.example", "delta.example", "charlie.example", "alpha.example"]


def test_default_is_ascending_by_domain_and_input_is_untouched():
    original = list(ALL)
    assert domains(sort_leads(ALL)) == sorted(domains(ALL))
    assert ALL == original


def test_business_name_ignores_case_and_spacing():
    assert domains(sort_leads([make("b.example", "apple"), make("a.example", "Banana")], "business_name")) == ["b.example", "a.example"]


def test_moments_compare_as_instants_not_as_text():
    plus3 = timezone(timedelta(hours=3))
    early = make("early.example", first=datetime(2026, 1, 1, 12, tzinfo=plus3))  # 09:00 UTC
    late = make("late.example", first=datetime(2026, 1, 1, 10, tzinfo=UTC))  # 10:00 UTC
    assert domains(sort_leads([late, early], "first_seen")) == ["early.example", "late.example"]


# ---------------------------------------------------------------- 2. descending
def test_descending_sort_for_every_field():
    assert domains(sort_leads(ALL, "domain", True)) == ["delta.example", "charlie.example", "bravo.example", "alpha.example"]
    assert domains(sort_leads(ALL, "business_name", True)) == ["delta.example", "charlie.example", "bravo.example", "alpha.example"]
    assert domains(sort_leads(ALL, "first_seen", True)) == ["delta.example", "alpha.example", "charlie.example", "bravo.example"]
    assert domains(sort_leads(ALL, "last_seen", True)) == ["alpha.example", "charlie.example", "delta.example", "bravo.example"]


def test_missing_values_come_last_in_both_directions():
    nameless, dated = make("nameless.example"), make("m.example", "M", at(1), at(2))
    for descending in (False, True):
        assert domains(sort_leads([nameless, dated], "business_name", descending)) == ["m.example", "nameless.example"]
        assert domains(sort_leads([nameless, dated], "first_seen", descending)) == ["m.example", "nameless.example"]
        assert domains(sort_leads([nameless, dated], "last_seen", descending)) == ["m.example", "nameless.example"]
    blank = make("blank.example", "   ")
    assert domains(sort_leads([blank, dated], "business_name")) == ["m.example", "blank.example"]


# ---------------------------------------------------------------- 3. tie breaking
def tied():
    return [make(f"{d}.example", "Same", at(1), at(2)) for d in ("echo", "alpha", "delta", "bravo", "charlie")]


@pytest.mark.parametrize("field", ["business_name", "first_seen", "last_seen"])
def test_equal_values_are_ordered_by_domain_in_both_directions(field):
    expected = ["alpha.example", "bravo.example", "charlie.example", "delta.example", "echo.example"]
    assert domains(sort_leads(tied(), field)) == expected
    assert domains(sort_leads(tied(), field, True)) == expected  # ties stay ascending, only the field reverses


def test_result_does_not_depend_on_input_order():
    leads = tied() + [make("zulu.example", "Other", at(5), at(6))]
    for field in ("business_name", "first_seen", "last_seen", "domain"):
        for descending in (False, True):
            reference = sort_leads(leads, field, descending)
            for perm in itertools.islice(itertools.permutations(leads), 120):
                assert sort_leads(perm, field, descending) == reference


def test_same_domain_different_website_is_still_deterministic():
    one, two = make("acme.example", "Acme"), BusinessLead(website="https://www.acme.example/", business_name="Acme")
    assert sort_leads([one, two], "business_name") == sort_leads([two, one], "business_name")


# ---------------------------------------------------------------- 4. page 1, 5. later pages
def numbered(count: int) -> list[BusinessLead]:
    return [make(f"site{n:02d}.example", f"Site {n:02d}") for n in range(count)]


def test_page_one():
    page = paginate(sort_leads(numbered(7)), 1, 3)
    assert domains(page.items) == ["site00.example", "site01.example", "site02.example"]
    assert (page.page, page.page_size, page.total, page.total_pages) == (1, 3, 7, 3)
    assert page.has_next and not page.has_previous


def test_later_pages_and_partial_last_page():
    leads = sort_leads(numbered(7))
    two, three = paginate(leads, 2, 3), paginate(leads, 3, 3)
    assert domains(two.items) == ["site03.example", "site04.example", "site05.example"]
    assert two.has_next and two.has_previous
    assert domains(three.items) == ["site06.example"]
    assert not three.has_next and three.has_previous
    seen = [lead for n in (1, 2, 3) for lead in paginate(leads, n, 3).items]
    assert seen == leads  # pages cover everything exactly once


def test_exact_multiple_and_single_page():
    assert paginate(numbered(6), 2, 3).total_pages == 2 and not paginate(numbered(6), 2, 3).has_next
    only = paginate(numbered(2), 1, 50)
    assert len(only.items) == 2 and only.total_pages == 1 and not only.has_next and not only.has_previous


def test_sort_and_paginate_combines_direction_and_page():
    page = sort_and_paginate(numbered(5), "domain", True, 2, 2)
    assert domains(page.items) == ["site02.example", "site01.example"]
    assert page.total == 5


# ---------------------------------------------------------------- 6. page beyond the result set
def test_page_beyond_result_set_is_empty_not_an_error():
    page = paginate(numbered(5), 99, 2)
    assert page.items == () and page.page == 99 and page.total == 5 and page.total_pages == 3
    assert not page.has_next and page.has_previous


# ---------------------------------------------------------------- 7. empty result
def test_empty_result_is_safe():
    page = sort_and_paginate([], "business_name", True, 1, 10)
    assert page.items == () and page.total == 0 and page.total_pages == 0
    assert not page.has_next and not page.has_previous
    assert sort_leads([]) == []
    assert paginate([], 5, 10).items == ()


def test_empty_filter_result_can_be_paged():
    assert sort_and_paginate(filter_leads(ALL, language="zz"), page=1, page_size=5).total == 0


# ---------------------------------------------------------------- 8. invalid page, 9. invalid page size
@pytest.mark.parametrize("bad", [0, -1, "1", 1.0, 2.5, None, True, [1]])
def test_invalid_page(bad):
    with pytest.raises(LeadPagingError, match="page") as info:
        paginate(ALL, bad, 10)
    assert isinstance(info.value, LeadDataError)
    with pytest.raises(LeadPagingError, match="page"):
        sort_and_paginate(ALL, page=bad)


@pytest.mark.parametrize("bad", [0, -5, 1001, "10", 10.0, None, False])
def test_invalid_page_size(bad):
    with pytest.raises(LeadPagingError, match="page_size"):
        paginate(ALL, 1, bad)
    with pytest.raises(LeadPagingError, match="page_size"):
        sort_and_paginate(ALL, page_size=bad)


def test_page_size_limits_are_inclusive_and_messages_name_the_value():
    assert paginate(ALL, 1, 1).page_size == 1 and paginate(ALL, 1, 1000).page_size == 1000
    with pytest.raises(LeadPagingError, match="at most 1000, got 1001"):
        paginate(ALL, 1, 1001)
    with pytest.raises(LeadPagingError, match="at least 1, got 0"):
        paginate(ALL, 0, 1)


def test_invalid_sort_arguments():
    with pytest.raises(LeadPagingError, match="valid: business_name, domain, first_seen, last_seen"):
        sort_leads(ALL, "score")
    with pytest.raises(LeadPagingError, match="sort_by"):
        sort_leads(ALL, None)
    with pytest.raises(LeadPagingError, match="descending"):
        sort_leads(ALL, "domain", "yes")
    with pytest.raises(LeadPagingError, match="BusinessLead"):
        sort_leads([A, "nope"])


# ---------------------------------------------------------------- read-only database use
def test_stored_leads_are_sorted_paged_and_not_modified(session):
    repo = LeadRepository(session)
    for lead in ALL:
        repo.save(lead)
    before = [lead.to_dict() for lead in repo.load_all(limit=100, offset=0)]
    page = sort_and_paginate_stored(repo, "last_seen", True, 1, 2)
    assert domains(page.items) == ["alpha.example", "charlie.example"] and page.total == 4
    assert sort_and_paginate_stored(repo, page=9).items == ()
    assert [lead.to_dict() for lead in repo.load_all(limit=100, offset=0)] == before


def test_bad_request_fails_before_the_database_is_read():
    class Boom:
        def load_all(self, **_):
            raise AssertionError("must not be read")

    for kwargs in ({"page": 0}, {"page_size": 0}, {"sort_by": "x"}, {"descending": 1}):
        with pytest.raises(LeadPagingError):
            sort_and_paginate_stored(Boom(), **kwargs)
