"""Phase 12.2: contact-data enrichment, tested through the existing lead model, aggregation and merge."""

from app.crawler.html_parser import PhoneNumber
from app.leads import BusinessLead, merge_leads
from app.leads.aggregate import aggregate_leads

SITE = "https://acme.example"


def lead(path="/", emails=(), phones=()):
    return BusinessLead(website=SITE, source_url=SITE + path, emails=emails, phones=phones)


def numbers(result):
    return [p.number for p in result.phones]


def test_email_enrichment():
    result = aggregate_leads([lead("/"), lead("/contact", emails=("info@acme.example",))])
    assert result.emails == ("info@acme.example",)


def test_phone_enrichment():
    result = aggregate_leads([lead("/"), lead("/contact", phones=(PhoneNumber("+15551234567", "+1 555 123 4567"),))])
    assert numbers(result) == ["+15551234567"]


def test_normalization():
    result = aggregate_leads([lead(emails=("  Info@ACME.example ",))])
    assert result.emails == ("info@acme.example",)
    result = aggregate_leads([
        lead("/a", phones=(PhoneNumber("+15551234567", "+1 555 123 4567"),)),
        lead("/b", phones=(PhoneNumber("+15551234567", "+1 (555) 123-4567"),)),
    ])
    assert numbers(result) == ["+15551234567"]


def test_duplicate_removal():
    result = aggregate_leads([
        lead("/a", emails=("a@acme.example", "A@acme.example")),
        lead("/b", emails=("a@acme.example",)),
    ])
    assert result.emails == ("a@acme.example",)
    assert len(result.phones) == 0


def test_multi_page_merging_keeps_all_unique_contacts():
    result = aggregate_leads([
        lead("/", emails=("a@acme.example",), phones=(PhoneNumber("+15551234567", "+15551234567"),)),
        lead("/contact", emails=("b@acme.example", "a@acme.example"), phones=(PhoneNumber("+442079460958", "+44 20 7946 0958"),)),
    ])
    assert result.emails == ("a@acme.example", "b@acme.example")
    assert numbers(result) == ["+15551234567", "+442079460958"]
    assert result.domain == "acme.example"  # still one lead for the site


def test_missing_contact_data_is_safe():
    result = aggregate_leads([lead("/"), lead("/about")])
    assert result.emails == () and result.phones == ()


def test_existing_contacts_are_preserved_when_new_page_has_none():
    stored = lead("/", emails=("a@acme.example",), phones=(PhoneNumber("+15551234567", "+15551234567"),))
    merged = merge_leads(stored, lead("/about"))
    assert merged.emails == ("a@acme.example",)
    assert numbers(merged) == ["+15551234567"]


def test_new_contacts_are_added_to_stored_lead():
    stored = lead("/", emails=("a@acme.example",))
    merged = merge_leads(stored, lead("/contact", emails=("b@acme.example",), phones=(PhoneNumber("+15551234567", "+15551234567"),)))
    assert merged.emails == ("a@acme.example", "b@acme.example")
    assert numbers(merged) == ["+15551234567"]
