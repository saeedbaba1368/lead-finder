"""Duplicate-URL detection on top of normalisation."""

from __future__ import annotations

from app.urls.domains import strip_www
from app.urls.normalize import normalize_url
from app.urls.policy import DEFAULT_POLICY, UrlPolicy

INDEX_DOCUMENTS: frozenset[str] = frozenset(
    {"index.html", "index.htm", "index.php", "index.asp", "index.aspx", "index.jsp",
     "default.htm", "default.html", "default.asp", "default.aspx"}
)


def dedupe_key(url: str, policy: UrlPolicy = DEFAULT_POLICY) -> str:
    """Key under which URLs that serve the same page collide.

    Beyond normalisation this ignores (by policy) the scheme, a leading `www.`, and a
    trailing index document (`/team/index.html` == `/team`). The key is for comparison
    only; the URL that gets fetched stays the normalised one.
    """
    n = normalize_url(url, policy=policy)
    host = strip_www(n.host) if policy.dedupe_ignore_www else n.host
    scheme = "" if policy.dedupe_ignore_scheme else f"{n.scheme}:"
    port = "" if n.port is None else f":{n.port}"
    segments = list(n.segments)
    if policy.dedupe_strip_index_documents and segments and segments[-1].lower() in INDEX_DOCUMENTS:
        segments.pop()
    path = "/" + "/".join(segments)
    query = f"?{n.query}" if n.query else ""
    return f"{scheme}//{host}{port}{path}{query}"


class SeenUrls:
    """Set of URLs already discovered/fetched, compared through `dedupe_key`."""

    def __init__(self, policy: UrlPolicy = DEFAULT_POLICY) -> None:
        self._policy = policy
        self._keys: set[str] = set()

    def __len__(self) -> int:
        return len(self._keys)

    def __contains__(self, url: str) -> bool:
        return dedupe_key(url, self._policy) in self._keys

    def add(self, url: str) -> bool:
        """Record `url`. Returns True if it was new, False if it was a duplicate."""
        key = dedupe_key(url, self._policy)
        if key in self._keys:
            return False
        self._keys.add(key)
        return True

    def register_canonical(self, page_url: str, canonical_url: str) -> bool:
        """Record that `page_url` declares `canonical_url`.

        Returns True if the canonical URL was already known as a *different* page, i.e.
        `page_url` is a duplicate whose content the caller should discard. Otherwise the
        canonical URL is marked as seen so it is not fetched again, and False is returned.
        """
        page_key = dedupe_key(page_url, self._policy)
        canonical_key = dedupe_key(canonical_url, self._policy)
        if canonical_key == page_key:
            return False
        if canonical_key in self._keys:
            return True
        self._keys.add(canonical_key)
        return False
