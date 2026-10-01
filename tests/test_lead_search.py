"""Phase 11.2: lead search (`leadfinder leads search`) on top of the Phase 10.3 filters. Read-only and offline (the
conftest blocks sockets). Pure tests use an in-memory stand-in with the `load_all` read API; CLI tests use SQLite files."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from app.cli.main import app
from app.crawler import PageCategory, PhoneNumber, SocialLink
from app.crawler.structured import Address
from app.leads import BusinessLead, LeadFilter, LeadFilterError, LeadRow, filter_leads, format_lead_listing, list_stored_leads
from tests.test_lead_listing import FakeRepository, cli_database, file_digest, stored_state

runner = CliRunner()
PHONE = [PhoneNumber("+14155550199", "+1 415 555 0199")]
IR_PHONE = [PhoneNumber("+982112345678", "۰۲۱-۱۲۳۴۵۶۷۸")]
ARABIC_YEH_IRAN = "\u0627\u064a\u0631\u0627\u0646"  # "ايران" typed with Arabic yeh; stored text uses Persian yeh

ACME = BusinessLead(website="https://acme.example/", business_name="Acme Plumbing", language="en", emails=["info@acme.example"],
                    phones=PHONE, page_type=PageCategory.HOMEPAGE, address=Address(city="Springfield", country="US"))
BOLT = BusinessLead(website="https://bolt.example/", business_name="Bolt Electric", language="en", emails=["hi@bolt.example"],
                    page_type=PageCategory.CONTACT, address=Address(city="London", country="UK"))
ARMAN = BusinessLead(website="https://arman.example/", business_name="لوله‌کشی آرمان", language="fa", phones=IR_PHONE,
                     page_type=PageCategory.ABOUT, address=Address(city="تهران", country="ایران"),
                     social_profiles=[SocialLink("instagram", "https://www.instagram.com/arman")])
CASPIAN = BusinessLead(website="https://caspian.example/", business_name="کاسپین", language="fa", emails=["a@caspian.example"],
                       phones=IR_PHONE, page_type=PageCategory.HOMEPAGE, address=Address(city="رشت", country="ایران"))
BARE = BusinessLead(website="https://bare.example/")
ALL = [ACME, ARMAN, BARE, BOLT, CASPIAN]  # domain order


def text(leads) -> str:
    return format_lead_listing([LeadRow.from_lead(lead) for lead in leads])


def search(repo_leads, **criteria) -> str:
    return format_lead_listing(list_stored_leads(FakeRepository(repo_leads), LeadFilter(**criteria) if criteria else None))


def cli(monkeypatch, tmp_path, *args, leads=ALL):
    cli_database(monkeypatch, tmp_path, leads)
    return runner.invoke(app, ["leads", "search", *args])


# ---------------------------------------------------------------- 1. no filter
def test_no_filter_lists_every_lead():
    assert search([CASPIAN, BARE, ACME, BOLT, ARMAN]) == text(ALL)
    assert list_stored_leads(FakeRepository(ALL), None) == list_stored_leads(FakeRepository(ALL))


def test_cli_no_filter_equals_leads_list(monkeypatch, tmp_path):
    result = cli(monkeypatch, tmp_path)
    assert result.exit_code == 0, result.output
    assert result.output == text(ALL) + "\n" == runner.invoke(app, ["leads", "list"]).output


# ---------------------------------------------------------------- 2. one filter
def test_one_filter():
    assert search(ALL, language="en") == text([ACME, BOLT])
    assert search(ALL, country="UK") == text([BOLT])
    assert search(ALL, page_type="homepage") == text([ACME, CASPIAN])
    assert search(ALL, page_type=PageCategory.ABOUT) == text([ARMAN])
    assert search(ALL, domain="https://www.acme.example/some/path") == text([ACME])  # canonical domain identity


def test_cli_one_filter(monkeypatch, tmp_path):
    result = cli(monkeypatch, tmp_path, "--language", "en")
    assert result.exit_code == 0, result.output
    assert result.output == text([ACME, BOLT]) + "\n"
    assert runner.invoke(app, ["leads", "search", "--domain", "acme.example"]).output == text([ACME]) + "\n"
    assert runner.invoke(app, ["leads", "search", "--page-type", "contact"]).output == text([BOLT]) + "\n"
    assert runner.invoke(app, ["leads", "search", "--country", "uk"]).output == text([BOLT]) + "\n"  # case-insensitive


# ---------------------------------------------------------------- 3. multiple filters
def test_multiple_filters_are_anded_and_repeated_values_are_alternatives():
    assert search(ALL, language="fa", has_email=True) == text([CASPIAN])
    assert search(ALL, language=["en", "fa"], has_phone=True) == text([ACME, ARMAN, CASPIAN])
    assert search(ALL, language="fa", country="ایران", city="رشت", page_type="homepage") == text([CASPIAN])
    combined = LeadFilter(language="fa") & LeadFilter(has_phone=True)
    assert list_stored_leads(FakeRepository(ALL), combined) == list_stored_leads(FakeRepository(ALL), LeadFilter(language="fa", has_phone=True))


def test_cli_multiple_filters(monkeypatch, tmp_path):
    result = cli(monkeypatch, tmp_path, "--language", "fa", "--has-email")
    assert result.exit_code == 0, result.output
    assert result.output == text([CASPIAN]) + "\n"
    both = runner.invoke(app, ["leads", "search", "--language", "en", "--language", "fa", "--has-phone"])
    assert both.output == text([ACME, ARMAN, CASPIAN]) + "\n"
    three = runner.invoke(app, ["leads", "search", "--language", "en", "--country", "us", "--no-email"])
    assert three.exit_code == 0 and three.output == "No leads match the filters.\n"


def test_search_uses_the_phase_10_3_filter_results():
    flt = LeadFilter(language="fa", has_email=False)
    assert [r.domain for r in list_stored_leads(FakeRepository(ALL), flt)] == [lead.domain for lead in filter_leads(ALL, flt)]


# ---------------------------------------------------------------- 4. has_email, 5. has_phone
def test_has_email():
    assert search(ALL, has_email=True) == text([ACME, BOLT, CASPIAN])
    assert search(ALL, has_email=False) == text([ARMAN, BARE])


def test_has_phone():
    assert search(ALL, has_phone=True) == text([ACME, ARMAN, CASPIAN])
    assert search(ALL, has_phone=False) == text([BARE, BOLT])


def test_cli_has_email_and_has_phone(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, ALL)
    run = lambda *a: runner.invoke(app, ["leads", "search", *a])  # noqa: E731
    assert run("--has-email").output == text([ACME, BOLT, CASPIAN]) + "\n"
    assert run("--no-email").output == text([ARMAN, BARE]) + "\n"
    assert run("--has-phone").output == text([ACME, ARMAN, CASPIAN]) + "\n"
    assert run("--no-phone").output == text([BARE, BOLT]) + "\n"
    assert run("--has-email", "--has-phone").output == text([ACME, CASPIAN]) + "\n"
    assert run("--has-social-profile").output == text([ARMAN]) + "\n"
    assert run("--no-social-profile").output == text([ACME, BARE, BOLT, CASPIAN]) + "\n"


# ---------------------------------------------------------------- 6. no results
def test_no_results_is_an_empty_list_not_an_error():
    assert list_stored_leads(FakeRepository(ALL), LeadFilter(language="zz")) == []
    assert search([], language="en") == "No leads found."


def test_cli_no_results_and_empty_database(monkeypatch, tmp_path):
    result = cli(monkeypatch, tmp_path, "--language", "zz")
    assert result.exit_code == 0 and result.output == "No leads match the filters.\n"
    no_match = runner.invoke(app, ["leads", "search", "--domain", "acme.example", "--domain", "acme.example", "--country", "UK"])
    assert no_match.exit_code == 0 and no_match.output == "No leads match the filters.\n"


def test_cli_empty_database(monkeypatch, tmp_path):
    result = cli(monkeypatch, tmp_path, leads=[])
    assert result.exit_code == 0 and result.output == "No leads found.\n"  # no filter: same as `leads list`
    filtered = runner.invoke(app, ["leads", "search", "--language", "en"])
    assert filtered.exit_code == 0 and filtered.output == "No leads match the filters.\n"


# ---------------------------------------------------------------- 7. invalid filter
@pytest.mark.parametrize("args, fragment", [
    (["--page-type", "spaceship"], "page_type: unknown page type 'spaceship' (valid:"),
    (["--language", ""], "language: the value must not be blank"),
    (["--language", "   "], "language: the value must not be blank"),
    (["--country", ""], "country: the value must not be blank"),
    (["--domain", ""], "domain: expected a domain or URL"),
    (["--language", "en", "--page-type", "nope"], "page_type"),
])
def test_cli_invalid_filter_value_is_a_clear_error(monkeypatch, tmp_path, args, fragment):
    result = cli(monkeypatch, tmp_path, *args)
    assert result.exit_code == 2, result.output
    assert "invalid filter:" in result.output and fragment in result.output
    assert "Traceback" not in result.output


def test_cli_invalid_filter_is_reported_before_the_database_is_used(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, create_tables=False)  # would be exit 1 (no leads table) if it got that far
    result = runner.invoke(app, ["leads", "search", "--page-type", "spaceship"])
    assert result.exit_code == 2 and "invalid filter:" in result.output


def test_cli_unknown_option_and_bad_flag_use_are_usage_errors(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, ALL)
    for args in (["--colour", "red"], ["--has-email", "--no-email-typo"], ["--language"]):
        assert runner.invoke(app, ["leads", "search", *args]).exit_code == 2


def test_invalid_filter_values_raise_the_10_3_error():
    for criteria in ({"page_type": "spaceship"}, {"language": ""}, {"has_email": "yes"}, {"domain": 5}):
        with pytest.raises(LeadFilterError):
            list_stored_leads(FakeRepository(ALL), LeadFilter(**criteria))


# ---------------------------------------------------------------- 8. Persian / Unicode
def test_persian_values():
    assert search(ALL, city="تهران") == text([ARMAN])
    assert search(ALL, country="ایران") == text([ARMAN, CASPIAN])
    assert search(ALL, country=ARABIC_YEH_IRAN) == text([ARMAN, CASPIAN])  # Arabic yeh matches Persian yeh
    assert search(ALL, language="fa", city="رشت") == text([CASPIAN])
    assert search(ALL, city="Tehran") == text([])  # exact matching: no transliteration


def test_cli_persian_values(monkeypatch, tmp_path):
    result = cli(monkeypatch, tmp_path, "--city", "تهران")
    assert result.exit_code == 0, result.output
    assert result.output == text([ARMAN]) + "\n" and "لوله‌کشی آرمان" in result.output and "\u200c" in result.output
    arabic = runner.invoke(app, ["leads", "search", "--country", ARABIC_YEH_IRAN, "--language", "fa"])
    assert arabic.output == text([ARMAN, CASPIAN]) + "\n"
    two_cities = runner.invoke(app, ["leads", "search", "--city", "تهران", "--city", "رشت"])
    assert two_cities.output == text([ARMAN, CASPIAN]) + "\n"
    none = runner.invoke(app, ["leads", "search", "--city", "Tehran"])
    assert none.exit_code == 0 and none.output == "No leads match the filters.\n"


# ---------------------------------------------------------------- no mutation, determinism, wiring
def test_search_does_not_change_the_database(monkeypatch, tmp_path):
    url = cli_database(monkeypatch, tmp_path, ALL)
    state, digest = stored_state(url), file_digest(url)
    for args in ([], ["--language", "fa"], ["--has-email", "--has-phone"], ["--language", "zz"], ["--city", "تهران"], ["--page-type", "bad"]):
        runner.invoke(app, ["leads", "search", *args])
    assert stored_state(url) == state and len(state) == 5
    assert file_digest(url) == digest


def test_in_memory_leads_are_not_changed_by_search():
    before = [lead.to_dict() for lead in ALL]
    search(ALL, language="fa", has_email=False)
    assert [lead.to_dict() for lead in ALL] == before


def test_cli_search_is_deterministic(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, ALL)
    outputs = {runner.invoke(app, ["leads", "search", "--language", "fa", "--has-phone"]).output for _ in range(3)}
    assert outputs == {text([ARMAN, CASPIAN]) + "\n"}


def test_search_help_lists_only_filters():
    result = runner.invoke(app, ["leads", "search", "--help"])
    assert result.exit_code == 0
    for option in ("--domain", "--language", "--country", "--city", "--page-type", "--has-email", "--has-phone"):
        assert option in result.output
    for option in ("--limit", "--score", "--format"):  # 11.4 added --sort-by/--page/--page-size
        assert option not in result.output
    assert "search" in runner.invoke(app, ["leads", "--help"]).output
