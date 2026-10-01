from app.leads.contact_enrichment import (
    enrich_contacts, merge_emails, merge_phones, normalize_email, normalize_phone,
)


def test_email_enrichment():
    assert merge_emails([], ["info@a.com"]) == ["info@a.com"]


def test_phone_enrichment():
    assert merge_phones([], ["+1 (555) 123-4567"]) == ["+15551234567"]


def test_email_normalization():
    assert normalize_email(" MAILTO:Info@A.COM?subject=hi ") == "info@a.com"
    assert normalize_email("not-an-email") is None


def test_phone_normalization():
    assert normalize_phone("tel:0044 20 7946 0958") == "+442079460958"
    assert normalize_phone("123") is None
    assert normalize_phone("021-1234567") == "0211234567"


def test_duplicate_removal():
    assert merge_emails(["a@x.com"], ["A@X.com", "a@x.com"]) == ["a@x.com"]
    assert merge_phones(["+15551234567"], ["+1 555-123-4567"]) == ["+15551234567"]


def test_multi_page_merge():
    e, p = enrich_contacts([], [], ["a@x.com"], ["+15551234567"])
    e, p = enrich_contacts(e, p, ["b@x.com", "a@x.com"], ["+442079460958"])
    assert e == ["a@x.com", "b@x.com"]
    assert p == ["+15551234567", "+442079460958"]


def test_missing_contact_data():
    assert enrich_contacts(None, None, None, None) == ([], [])
    assert enrich_contacts([], [], [None, ""], ["", None]) == ([], [])


def test_preserves_existing_data():
    e, p = enrich_contacts(["a@x.com"], ["+15551234567"], None, [])
    assert e == ["a@x.com"] and p == ["+15551234567"]
    e, p = enrich_contacts(["a@x.com"], ["+15551234567"], ["bad"], ["12"])
    assert e == ["a@x.com"] and p == ["+15551234567"]
