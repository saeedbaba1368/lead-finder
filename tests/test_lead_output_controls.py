"""Phase 11.4: pagination and sorting in the Lead interface (`leads list` / `leads search` --sort-by/--desc/--page/--page-size).

Nothing is re-implemented: the library tests check `list_leads_page` against the Phase 10.4 `sort_and_paginate`; the CLI
tests use SQLite files (helpers of `test_lead_listing`, imported lazily). Read-only and offline.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.leads import BusinessLead, LeadFilter, LeadPagingError, LeadRow, format_lead_listing, list_leads_page, list_stored_leads
from app.leads.listing import COLUMNS, EMPTY_MESSAGE, LeadListing
from app.leads.paging import MAX_PAGE_SIZE, sort_and_paginate

HEADER = "\t".join(COLUMNS)


def at(day: int) -> datetime:
    return datetime(2026, 1, day, 9, 0, tzinfo=UTC)


# Five leads. Names: two share "Delta"; one has no name. first_seen: two share day 2.
A = BusinessLead(website="https://a.example/", business_name="Delta", first_seen=at(3), last_seen=at(9), language="en")
B = BusinessLead(website="https://b.example/", business_name="Alpha", first_seen=at(2), last_seen=at(8), language="en")
C = BusinessLead(website="https://c.example/", business_name="delta", first_seen=at(2), last_seen=at(7), language="fa")
D = BusinessLead(website="https://d.example/", business_name="Charlie", first_seen=at(1), last_seen=at(6), language="en")
E = BusinessLead(website="https://e.example/")
ALL = [A, B, C, D, E]  # domain order


class FakeRepository:
    def __init__(self, leads):
        self.leads = list(leads)

    def load_all(self, *, limit=100, offset=0):
        return self.leads[offset:offset + limit]


def domains(listing: LeadListing) -> list[str]:
    return [row.domain for row in listing.rows]


def page(leads=ALL, **kw) -> LeadListing:
    return list_leads_page(FakeRepository(leads), **kw)


def cli_db(monkeypatch, tmp_path, leads=ALL, **kw) -> str:
    from tests.test_lead_listing import cli_database

    return cli_database(monkeypatch, tmp_path, leads, **kw)


def cli_invoke(args):
    from typer.testing import CliRunner

    from app.cli.main import app

    return CliRunner().invoke(app, args)


def cli_run(monkeypatch, tmp_path, args, leads=ALL):
    """One database holding `leads`, one invocation."""
    cli_db(monkeypatch, tmp_path, leads)
    return cli_invoke(args)


def cli_domains(result) -> list[str]:
    lines = result.stdout.splitlines()
    return [line.split("\t")[1] for line in lines[1:]]


# ---------------------------------------------------------------- 0. defaults unchanged
def test_without_options_the_listing_is_unchanged():
    listing = page()
    assert domains(listing) == ["a.example", "b.example", "c.example", "d.example", "e.example"]
    assert listing.rows == tuple(list_stored_leads(FakeRepository(ALL)))
    assert (listing.page, listing.page_size, listing.total_pages, listing.total) == (None, None, None, 5)
    assert listing.summary is None


# ---------------------------------------------------------------- 1. first page
def test_first_page():
    listing = page(page=1, page_size=2)
    assert domains(listing) == ["a.example", "b.example"]
    assert (listing.page, listing.page_size, listing.total, listing.total_pages) == (1, 2, 5, 3)
    assert listing.summary == "page 1 of 3 (2 of 5 lead(s), page size 2)"


def test_cli_first_page(monkeypatch, tmp_path):
    result = cli_run(monkeypatch, tmp_path, ["leads", "list", "--page", "1", "--page-size", "2"])
    assert result.exit_code == 0, result.output
    assert cli_domains(result) == ["a.example", "b.example"]
    assert result.stdout.splitlines()[0] == HEADER
    assert result.stderr.strip() == "page 1 of 3 (2 of 5 lead(s), page size 2)"


# ---------------------------------------------------------------- 2. second page
def test_second_page_continues_after_the_first_without_overlap():
    first, second, third = (page(page=n, page_size=2) for n in (1, 2, 3))
    assert domains(second) == ["c.example", "d.example"]
    assert domains(third) == ["e.example"]
    assert domains(first) + domains(second) + domains(third) == [lead.domain for lead in ALL]


def test_cli_second_page(monkeypatch, tmp_path):
    result = cli_run(monkeypatch, tmp_path, ["leads", "list", "--page", "2", "--page-size", "2"])
    assert result.exit_code == 0, result.output
    assert cli_domains(result) == ["c.example", "d.example"]


# ---------------------------------------------------------------- 3. page beyond results
def test_page_beyond_results_is_empty_not_an_error():
    listing = page(page=9, page_size=2)
    assert listing.rows == () and listing.total == 5 and listing.total_pages == 3
    assert listing.empty_message == "No leads on page 9 (5 lead(s) in 3 page(s) of 2)."
    assert listing.summary is None


def test_empty_database_with_paging():
    listing = page([], page=1, page_size=10)
    assert listing.rows == () and listing.total == 0 and listing.total_pages == 0
    assert listing.empty_message == EMPTY_MESSAGE


def test_cli_page_beyond_results(monkeypatch, tmp_path):
    result = cli_run(monkeypatch, tmp_path, ["leads", "list", "--page", "9", "--page-size", "2"])
    assert result.exit_code == 0, result.output
    assert result.stdout == "No leads on page 9 (5 lead(s) in 3 page(s) of 2).\n"


def test_cli_search_page_beyond_filtered_results(monkeypatch, tmp_path):
    result = cli_run(monkeypatch, tmp_path, ["leads", "search", "--language", "fa", "--page", "4"])
    assert result.exit_code == 0, result.output
    assert result.stdout == "No leads on page 4 (1 lead(s) in 1 page(s) of 50).\n"
    none = cli_invoke(["leads", "search", "--language", "de", "--page", "1"])
    assert none.exit_code == 0 and none.stdout == "No leads match the filters.\n"


# ---------------------------------------------------------------- 4. page size
def test_page_size():
    assert len(page(page_size=1).rows) == 1
    assert len(page(page_size=4).rows) == 4
    big = page(page_size=MAX_PAGE_SIZE)
    assert len(big.rows) == 5 and big.total_pages == 1 and big.page == 1


def test_page_size_alone_or_page_alone_turns_paging_on_with_defaults():
    assert page(page_size=2).page == 1
    only_page = page(page=1)
    assert only_page.page_size == 50 and len(only_page.rows) == 5


def test_cli_page_size(monkeypatch, tmp_path):
    result = cli_run(monkeypatch, tmp_path, ["leads", "list", "--page-size", "3"])
    assert result.exit_code == 0, result.output
    assert cli_domains(result) == ["a.example", "b.example", "c.example"]


# ---------------------------------------------------------------- 5/6. sorting
def test_ascending_sort_by_business_name_unknown_last():
    assert domains(page(sort_by="business_name")) == ["b.example", "d.example", "a.example", "c.example", "e.example"]


def test_descending_sort_by_business_name_unknown_still_last():
    assert domains(page(sort_by="business_name", descending=True)) == [
        "a.example", "c.example", "d.example", "b.example", "e.example"]


def test_sort_by_first_seen_both_directions():
    assert domains(page(sort_by="first_seen")) == ["d.example", "b.example", "c.example", "a.example", "e.example"]
    assert domains(page(sort_by="first_seen", descending=True)) == [
        "a.example", "b.example", "c.example", "d.example", "e.example"]


def test_sort_by_last_seen_and_domain():
    assert domains(page(sort_by="last_seen")) == ["d.example", "c.example", "b.example", "a.example", "e.example"]
    assert domains(page(sort_by="domain", descending=True)) == [
        "e.example", "d.example", "c.example", "b.example", "a.example"]


def test_cli_sort_ascending_and_descending(monkeypatch, tmp_path):
    asc = cli_run(monkeypatch, tmp_path, ["leads", "list", "--sort-by", "business_name", "--asc"])
    assert asc.exit_code == 0, asc.output
    assert cli_domains(asc) == ["b.example", "d.example", "a.example", "c.example", "e.example"]
    desc = cli_invoke(["leads", "list", "--sort-by", "business_name", "--desc"])
    assert cli_domains(desc) == ["a.example", "c.example", "d.example", "b.example", "e.example"]


def test_sorted_pages_follow_the_sorted_order():
    pages = [page(sort_by="first_seen", descending=True, page=n, page_size=2) for n in (1, 2, 3)]
    assert [d for p in pages for d in domains(p)] == ["a.example", "b.example", "c.example", "d.example", "e.example"]


def test_reuses_the_10_4_functionality():
    expected = sort_and_paginate(ALL, "last_seen", True, 2, 2)
    got = page(sort_by="last_seen", descending=True, page=2, page_size=2)
    assert got.rows == tuple(LeadRow.from_lead(lead) for lead in expected.items)
    assert (got.total, got.total_pages) == (expected.total, expected.total_pages)


def test_filter_then_sort_then_page():
    listing = page(flt=LeadFilter(language="en"), sort_by="first_seen", page=1, page_size=2)
    assert domains(listing) == ["d.example", "b.example"] and listing.total == 3


def test_cli_search_with_filter_sort_and_page(monkeypatch, tmp_path):
    args = ["leads", "search", "--language", "en", "--sort-by", "first_seen", "--page-size", "2", "--page", "2"]
    result = cli_run(monkeypatch, tmp_path, args)
    assert result.exit_code == 0, result.output
    assert cli_domains(result) == ["a.example"]


# ---------------------------------------------------------------- 7. deterministic tie-breaking
def test_ties_are_broken_by_domain_whatever_the_stored_order_and_direction():
    import itertools

    for order in itertools.permutations(ALL):
        # "Delta"/"delta" tie (a, c) and first_seen day 2 tie (b, c): the domain decides, ascending in both directions
        assert domains(page(list(order), sort_by="business_name")) == ["b.example", "d.example", "a.example", "c.example", "e.example"]
        assert domains(page(list(order), sort_by="business_name", descending=True)) == [
            "a.example", "c.example", "d.example", "b.example", "e.example"]
        assert domains(page(list(order), sort_by="first_seen")) == ["d.example", "b.example", "c.example", "a.example", "e.example"]


def test_ties_never_move_a_lead_between_pages():
    twins = [BusinessLead(website=f"https://{c}.example/", business_name="Same", first_seen=at(1)) for c in "dcba"]
    seen = [d for n in (1, 2, 3, 4) for d in domains(page(twins, sort_by="business_name", page=n, page_size=1))]
    assert seen == ["a.example", "b.example", "c.example", "d.example"]


def test_repeated_runs_give_identical_text():
    texts = {format_lead_listing(list(page(sort_by="last_seen", page=2, page_size=2).rows)) for _ in range(5)}
    assert len(texts) == 1


def test_cli_ties_are_deterministic(monkeypatch, tmp_path):
    twins = [BusinessLead(website=f"https://{c}.example/", business_name="Same") for c in "dcba"]
    result = cli_run(monkeypatch, tmp_path, ["leads", "list", "--sort-by", "business_name", "--desc"], leads=twins)
    assert cli_domains(result) == ["a.example", "b.example", "c.example", "d.example"]


# ---------------------------------------------------------------- 8. invalid values
@pytest.mark.parametrize("kwargs, text", [
    ({"page": 0}, "page: must be at least 1"),
    ({"page": -3}, "page: must be at least 1"),
    ({"page_size": 0}, "page_size: must be at least 1"),
    ({"page_size": MAX_PAGE_SIZE + 1}, f"page_size: must be at most {MAX_PAGE_SIZE}"),
    ({"page": "2"}, "page: expected a whole number"),
    ({"page": True}, "page: expected a whole number"),
    ({"page_size": 1.5}, "page_size: expected a whole number"),
    ({"sort_by": "score"}, "sort_by: unknown field 'score'"),
    ({"sort_by": ""}, "sort_by: unknown field"),
    ({"descending": "yes"}, "descending: expected True or False"),
])
def test_invalid_values_raise_clear_errors_before_reading(kwargs, text):
    class Exploding:
        def load_all(self, **_):
            raise AssertionError("the database must not be read for an invalid request")

    with pytest.raises(LeadPagingError, match=text):
        list_leads_page(Exploding(), **kwargs)


@pytest.mark.parametrize("args, text", [
    (["--page", "0"], "invalid paging: page: must be at least 1"),
    (["--page-size", "0"], "invalid paging: page_size: must be at least 1"),
    (["--page-size", "1001"], "page_size: must be at most 1000"),
    (["--sort-by", "score"], "sort_by: unknown field 'score' (valid: business_name, domain, first_seen, last_seen)"),
    (["--sort-by", "rank"], "sort_by: unknown field 'rank'"),
])
def test_cli_invalid_values_exit_2_without_touching_the_database(monkeypatch, tmp_path, args, text):
    cli_db(monkeypatch, tmp_path, [A])
    for command in (["leads", "list"], ["leads", "search"]):
        result = cli_invoke([*command, *args])
        assert result.exit_code == 2, result.output
        assert text in result.stderr and result.stdout == ""


def test_cli_non_numeric_page_is_a_usage_error(monkeypatch, tmp_path):
    cli_db(monkeypatch, tmp_path, [A])
    for args in (["--page", "abc"], ["--page-size", "1.5"]):
        assert cli_invoke(["leads", "list", *args]).exit_code == 2


def test_cli_invalid_paging_is_reported_even_without_a_leads_table(monkeypatch, tmp_path):
    cli_db(monkeypatch, tmp_path, (), create_tables=False)
    assert cli_invoke(["leads", "list", "--page", "0"]).exit_code == 2


# ---------------------------------------------------------------- no mutation
def test_stored_data_is_not_mutated():
    leads = list(ALL)
    snapshot = [lead.to_dict() for lead in leads]
    repo = FakeRepository(leads)
    list_leads_page(repo, sort_by="last_seen", descending=True, page=2, page_size=2)
    assert repo.leads == ALL and [lead.to_dict() for lead in repo.leads] == snapshot


def test_cli_does_not_change_the_database(monkeypatch, tmp_path):
    from tests.test_lead_listing import file_digest, stored_state

    url = cli_db(monkeypatch, tmp_path)
    before, digest = stored_state(url), file_digest(url)
    for args in (["--sort-by", "last_seen", "--desc", "--page", "2", "--page-size", "2"], ["--page", "99"]):
        assert cli_invoke(["leads", "list", *args]).exit_code == 0
    assert stored_state(url) == before and file_digest(url) == digest
