import ipaddress
import socket

import pytest

from app.urls import (
    DEFAULT_POLICY,
    RejectReason,
    UrlEvaluator,
    UrlPolicy,
    UrlRejected,
    check_resolved_addresses,
    host_block_reason,
    ip_block_reason,
    normalize_url,
    resolve_and_check,
    system_resolver,
)

P = DEFAULT_POLICY


def blocked(url: str, policy: UrlPolicy = P):
    n = normalize_url(url, policy=policy.with_changes(allowed_ports=None))
    return host_block_reason(n.host, policy)


class TestIpClassification:
    @pytest.mark.parametrize(
        "ip,reason",
        [
            ("127.0.0.1", "loopback"), ("127.255.255.254", "loopback"), ("::1", "loopback"),
            ("10.0.0.1", "private"), ("172.16.0.1", "private"), ("172.31.255.255", "private"),
            ("192.168.1.1", "private"), ("fc00::1", "private"), ("fd12:3456::1", "private"),
            ("169.254.1.1", "link_local"), ("fe80::1", "link_local"),
            ("169.254.169.254", "metadata"), ("169.254.170.2", "metadata"),
            ("100.100.100.200", "metadata"), ("168.63.129.16", "metadata"), ("192.0.0.192", "metadata"),
            ("fd00:ec2::254", "metadata"),
            ("100.64.0.1", "cgnat"), ("0.0.0.0", "unspecified"), ("0.1.2.3", "unspecified"), ("::", "unspecified"),
            ("224.0.0.1", "multicast"), ("ff02::1", "multicast"),
            ("240.0.0.1", "reserved"), ("255.255.255.255", "reserved"),
            ("198.18.0.1", "benchmarking"), ("192.0.2.1", "documentation"), ("2001:db8::1", "documentation"),
            ("2001:0:4136:e378:8000:63bf:3fff:fdd2", "teredo"),
        ],
    )
    def test_blocked(self, ip, reason):
        assert ip_block_reason(ipaddress.ip_address(ip)) == reason

    @pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "172.32.0.1", "172.15.255.255", "2606:4700:4700::1111", "100.63.255.255", "100.128.0.1"])
    def test_public_addresses_allowed(self, ip):
        assert ip_block_reason(ipaddress.ip_address(ip)) is None

    @pytest.mark.parametrize(
        "ip",
        [
            "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",  # IPv4-mapped
            "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe",  # NAT64
            "2002:7f00:1::", "2002:a9fe:a9fe::1",  # 6to4
            "::127.0.0.1", "::10.0.0.1",  # IPv4-compatible
            "::ffff:0:7f00:1", "::ffff:0:a9fe:a9fe",  # IPv4-translated (SIIT)
        ],
    )
    def test_ipv6_wrapping_ipv4_is_unwrapped(self, ip):
        assert ip_block_reason(ipaddress.ip_address(ip)) is not None

    def test_ipv6_wrapping_public_ipv4_allowed(self):
        assert ip_block_reason(ipaddress.ip_address("::ffff:8.8.8.8")) is None


class TestUrlHosts:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost/", "http://LOCALHOST:80/", "http://localhost./", "http://foo.localhost/",
            "http://127.0.0.1/", "http://127.1/", "http://2130706433/", "http://0x7f.0.0.1/", "http://0177.0.0.1/",
            "http://0/", "http://0.0.0.0/", "http://[::1]/", "http://[::]/", "http://[::ffff:127.0.0.1]/",
            "http://[::ffff:7f00:1]/", "http://[::ffff:0:127.0.0.1]/", "http://10.1.2.3/", "http://192.168.0.1/", "http://172.20.0.5/",
            "http://169.254.169.254/latest/meta-data/", "http://2852039166/", "http://0xa9fea9fe/",
            "http://metadata.google.internal/", "http://metadata/", "http://instance-data/",
            "http://foo.internal/", "http://printer.local/", "http://nas.lan/", "http://router.home.arpa/",
            "http://kubernetes.default.svc/", "http://api.default.svc.cluster.local/",
            "http://intranet/", "http://db/", "http://%6c%6f%63%61%6c%68%6f%73%74/",
            "http://127.0.0.1.nip.io/", "http://a.b.sslip.io/", "http://localtest.me/", "http://x.lvh.me/",
            "http://1.0.0.127.in-addr.arpa/",
        ],
    )
    def test_blocked_urls(self, url):
        assert blocked(url) is not None, url

    @pytest.mark.parametrize(
        "url", ["http://example.com/", "https://www.example.co.uk/", "http://8.8.8.8/", "https://[2606:4700:4700::1111]/", "http://localhost.example.com/", "http://notlocalhost.com/"]
    )
    def test_allowed_urls(self, url):
        assert blocked(url) is None, url

    def test_single_label_block_is_configurable(self):
        p = UrlPolicy(block_single_label_hosts=False)
        assert blocked("http://intranet/", p) is None
        assert blocked("http://localhost/", p) is not None  # still an internal name

    def test_allow_private_networks_lifts_private_but_not_metadata(self):
        p = UrlPolicy(ssrf_allow_private_networks=True)
        assert blocked("http://127.0.0.1/", p) is None
        assert blocked("http://10.0.0.5/", p) is None
        assert blocked("http://localhost/", p) is None
        assert blocked("http://169.254.169.254/", p) == "metadata"
        assert blocked("http://[::ffff:169.254.169.254]/", p) == "metadata"
        assert blocked("http://168.63.129.16/", p) == "metadata"


class TestResolvedAddresses:
    def test_public_addresses_pass(self):
        assert check_resolved_addresses(["93.184.216.34", "2606:2800:220:1::1"], P) is None

    def test_any_bad_address_blocks(self):
        assert check_resolved_addresses(["93.184.216.34", "10.0.0.1"], P) == "private"

    def test_empty_and_garbage(self):
        assert check_resolved_addresses([], P) == "unresolvable"
        assert check_resolved_addresses(["not-an-ip"], P) == "invalid_address"

    def test_zone_id_stripped(self):
        assert check_resolved_addresses(["fe80::1%eth0"], P) == "link_local"

    def test_private_allowed_but_metadata_never(self):
        p = UrlPolicy(ssrf_allow_private_networks=True)
        assert check_resolved_addresses(["10.0.0.1"], p) is None
        assert check_resolved_addresses(["169.254.169.254"], p) == "metadata"

    def test_resolve_and_check_returns_validated_addresses(self):
        assert resolve_and_check("example.com", lambda h: ["93.184.216.34"], P) == ["93.184.216.34"]

    def test_dns_pointing_at_internal_ip_is_blocked(self):
        with pytest.raises(UrlRejected) as info:
            resolve_and_check("innocent.example.com", lambda h: ["127.0.0.1"], P)
        assert info.value.reason == RejectReason.SSRF_BLOCKED
        assert "loopback" in info.value.detail

    def test_mixed_rebinding_style_answer_blocked(self):
        with pytest.raises(UrlRejected):
            resolve_and_check("x.example.com", lambda h: ["93.184.216.34", "169.254.169.254"], P)

    def test_resolver_failure_is_blocked(self):
        def boom(host):
            raise socket.gaierror("no such host")

        with pytest.raises(UrlRejected) as info:
            resolve_and_check("nx.example.com", boom, P)
        assert info.value.detail == "unresolvable"

    def test_static_block_short_circuits_resolver(self):
        def never(host):
            raise AssertionError("resolver must not be called")

        with pytest.raises(UrlRejected):
            resolve_and_check("localhost", never, P)

    def test_ip_literal_skips_resolver(self):
        def never(host):
            raise AssertionError("resolver must not be called")

        assert resolve_and_check("8.8.8.8", never, P) == ["8.8.8.8"]
        assert resolve_and_check("[2606:4700:4700::1111]", never, P) == ["2606:4700:4700::1111"]

    def test_system_resolver_uses_getaddrinfo(self, monkeypatch):
        def fake(host, port, type=0):  # no real DNS
            return [(2, 1, 6, "", ("93.184.216.34", 0)), (2, 1, 6, "", ("93.184.216.34", 0)), (10, 1, 6, "", ("fe80::1%lo", 0, 0, 1))]

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        assert system_resolver("example.com") == ["93.184.216.34", "fe80::1"]


class TestEvaluatorIntegration:
    def make(self, **kw):
        return UrlEvaluator(UrlPolicy(**kw), seed_url="https://example.com")

    @pytest.mark.parametrize("url", ["http://localhost/admin", "http://127.0.0.1:80/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "http://192.168.1.1/"])
    def test_rejected_as_ssrf(self, url):
        d = self.make().evaluate(url)
        assert not d.allowed and d.reason == RejectReason.SSRF_BLOCKED

    def test_ssrf_reported_before_scope(self):
        d = self.make().evaluate("http://localhost/")
        assert d.reason == RejectReason.SSRF_BLOCKED  # not OUT_OF_SCOPE

    def test_redirect_to_internal_address_blocked(self):
        ev = self.make()
        assert ev.evaluate_redirect("https://example.com/a", "/b").allowed
        d = ev.evaluate_redirect("https://example.com/a", "http://169.254.169.254/latest/meta-data/")
        assert d.reason == RejectReason.SSRF_BLOCKED
        d = ev.evaluate_redirect("https://example.com/a", "http://0x7f000001/")
        assert d.reason == RejectReason.SSRF_BLOCKED

    def test_redirect_out_of_scope_blocked(self):
        d = self.make().evaluate_redirect("https://example.com/a", "https://other.org/")
        assert d.reason == RejectReason.OUT_OF_SCOPE

    def test_internal_seed_refused(self):
        with pytest.raises(UrlRejected) as info:
            UrlEvaluator(seed_url="http://localhost:80/")
        assert info.value.reason == RejectReason.SSRF_BLOCKED

    def test_private_seed_allowed_with_flag(self):
        ev = UrlEvaluator(UrlPolicy(ssrf_allow_private_networks=True), seed_url="http://127.0.0.1/")
        assert ev.evaluate("http://127.0.0.1/team").allowed
