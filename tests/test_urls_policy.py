import random
import string

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.urls import DEFAULT_POLICY, RejectReason, UrlEvaluator, UrlPolicy, UrlRejected, normalize_url
from app.urls.policy import csv_set, parse_ports


class TestPolicyObject:
    def test_defaults(self):
        p = UrlPolicy()
        assert p.allowed_ports == frozenset({80, 443})
        assert p.subdomain_mode == "relevant" and p.trailing_slash == "strip"
        assert not p.ssrf_allow_private_networks and not p.allow_pdf

    def test_is_immutable_and_hashable(self):
        with pytest.raises(AttributeError):
            DEFAULT_POLICY.max_url_length = 1  # type: ignore[misc]
        assert hash(UrlPolicy()) == hash(UrlPolicy())

    def test_with_changes_validates(self):
        assert UrlPolicy().with_changes(max_path_depth=3).max_path_depth == 3
        with pytest.raises(ValueError):
            UrlPolicy().with_changes(max_path_depth=0)

    @pytest.mark.parametrize(
        "kw",
        [dict(max_url_length=0), dict(max_page_number=-1), dict(max_segment_repeats=1), dict(calendar_years_back=-1),
         dict(subdomain_mode="bogus"), dict(trailing_slash="add"), dict(max_query_variants_per_path=0)],
    )
    def test_invalid_values_rejected(self, kw):
        with pytest.raises(ValueError):
            UrlPolicy(**kw)

    def test_helpers(self):
        assert csv_set(" A.com, b.com ,,") == frozenset({"a.com", "b.com"})
        assert csv_set("") == frozenset() and csv_set(None) == frozenset()
        assert parse_ports("80, 443,8080") == frozenset({80, 443, 8080})
        assert parse_ports("*") is None and parse_ports("") is None and parse_ports(None) is None
        with pytest.raises(ValueError):
            parse_ports("0")
        with pytest.raises(ValueError):
            parse_ports("70000")


class TestFromSettings:
    def test_defaults_match_policy_defaults(self):
        assert UrlPolicy.from_settings(Settings(_env_file=None)) == UrlPolicy()

    def test_environment_overrides(self, monkeypatch):
        env = {
            "APP_URL_ALLOWED_PORTS": "80,443,8080", "APP_URL_SUBDOMAIN_MODE": "all",
            "APP_URL_SCOPE_DOMAINS": "Example.org, other.net", "APP_URL_BLOCKED_DOMAINS": "facebook.com",
            "APP_URL_EXTRA_TRACKING_PARAMS": "ref,src", "APP_URL_EXTRA_PUBLIC_SUFFIXES": "corp.example",
            "APP_URL_TRAILING_SLASH": "keep", "APP_URL_ALLOW_PDF": "true", "APP_URL_MAX_PATH_DEPTH": "5",
            "APP_URL_MAX_PAGE_NUMBER": "10", "APP_URL_CALENDAR_YEARS_BACK": "2", "APP_URL_MAX_LENGTH": "500",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        p = UrlPolicy.from_settings(Settings(_env_file=None))
        assert p.allowed_ports == frozenset({80, 443, 8080})
        assert p.subdomain_mode == "all" and p.trailing_slash == "keep" and p.allow_pdf
        assert p.scope_domains == frozenset({"example.org", "other.net"})
        assert p.blocked_domains == frozenset({"facebook.com"})
        assert p.extra_tracking_params == frozenset({"ref", "src"})
        assert p.extra_public_suffixes == frozenset({"corp.example"})
        assert (p.max_path_depth, p.max_page_number, p.calendar_years_back, p.max_url_length) == (5, 10, 2, 500)

    def test_any_port_via_star(self, monkeypatch):
        monkeypatch.setenv("APP_URL_ALLOWED_PORTS", "*")
        assert UrlPolicy.from_settings(Settings(_env_file=None)).allowed_ports is None

    @pytest.mark.parametrize("name,value", [("APP_URL_SUBDOMAIN_MODE", "nope"), ("APP_URL_TRAILING_SLASH", "add"), ("APP_URL_MAX_PATH_DEPTH", "0"), ("APP_URL_MAX_LENGTH", "5")])
    def test_invalid_settings_fail_fast(self, monkeypatch, name, value):
        monkeypatch.setenv(name, value)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_private_network_flag_forbidden_in_production(self, monkeypatch):
        monkeypatch.setenv("APP_URL_SSRF_ALLOW_PRIVATE_NETWORKS", "true")
        monkeypatch.setenv("APP_ENV", "production")
        with pytest.raises(ValidationError):
            Settings(_env_file=None)
        monkeypatch.setenv("APP_ENV", "development")
        assert Settings(_env_file=None).url_ssrf_allow_private_networks is True

    def test_settings_drive_behaviour(self, monkeypatch):
        monkeypatch.setenv("APP_URL_TRAILING_SLASH", "keep")
        monkeypatch.setenv("APP_URL_BLOCKED_DOMAINS", "bad.com")
        ev = UrlEvaluator(UrlPolicy.from_settings(Settings(_env_file=None)), seed_url="https://example.com")
        assert ev.evaluate("https://example.com/team/").url == "https://example.com/team/"
        assert ev.evaluate("https://bad.com/").reason == RejectReason.BLOCKED_DOMAIN

    def test_cli_config_shows_url_settings(self):
        from typer.testing import CliRunner

        from app.cli.main import app

        out = CliRunner().invoke(app, ["config"]).stdout
        assert "url_subdomain_mode" in out and "url_allowed_ports" in out


def test_env_example_documents_every_url_setting():
    from pathlib import Path

    text = (Path(__file__).parent.parent / ".env.example").read_text()
    for name in Settings.model_fields:
        if name.startswith("url_"):
            assert f"APP_{name.upper()}=" in text, name


def test_configuration_doc_documents_every_url_setting():
    from pathlib import Path

    text = (Path(__file__).parent.parent / "docs" / "configuration.md").read_text()
    for name in Settings.model_fields:
        if name.startswith("url_"):
            assert f"`APP_{name.upper()}`" in text, name


class TestRobustness:
    def test_fuzzed_input_never_raises_unexpectedly(self):
        rng = random.Random(1234)
        alphabet = string.printable + "éü€\u202e\u0000%[]@\\:/?#&=;.。ｅ"
        ev = UrlEvaluator(seed_url="https://example.com", clock=lambda: __import__("datetime").date(2026, 9, 30))
        for _ in range(4000):
            raw = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
            base = rng.choice([None, "https://example.com/a/b", "http://[::1]/"])
            decision = ev.evaluate(raw, base=base)
            if decision.allowed:
                # anything admitted must be a normal, idempotent http(s) URL
                again = normalize_url(decision.url)
                assert again.url == decision.url
                assert again.scheme in ("http", "https")

    def test_fuzzed_url_shaped_input(self):
        rng = random.Random(99)
        pieces = ["http://", "https://", "//", "[::1]", "127.0.0.1", "0x7f.1", "example.com", "www.", "@", ":", ":80", ":0", "/", "..", "%2e", "%00", "?", "#", "\\", "a", "1", ".", "%", "é"]
        ev = UrlEvaluator(seed_url="https://example.com")
        for _ in range(4000):
            raw = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 10)))
            d = ev.evaluate(raw)
            if d.allowed:
                assert normalize_url(d.url).url == d.url
                assert not d.url.startswith(("http://127.", "http://localhost"))

    def test_pathological_lengths_are_fast_and_safe(self):
        ev = UrlEvaluator(seed_url="https://example.com")
        assert ev.evaluate("https://example.com/" + "a/" * 5000).reason == RejectReason.TOO_LONG
        assert ev.evaluate("https://example.com/?" + "a=1&" * 5000).reason == RejectReason.TOO_LONG
        assert ev.evaluate("%" * 100_000).reason == RejectReason.TOO_LONG

    def test_normalize_raises_only_urlrejected(self):
        for raw in ["http://[", "http://%zz/", "http://a\x00b/", "\ud800", "http://" + "é" * 300]:
            try:
                normalize_url(raw)
            except UrlRejected:
                pass
