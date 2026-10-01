"""Built-in public-suffix data.

The full Public Suffix List is intentionally not bundled: the project must work offline
without extra dependencies. This is a compact, hand-maintained subset covering the
multi-label country suffixes that matter most in practice, plus hosting platforms where
sibling subdomains belong to *different* owners (so `a.github.io` and `b.github.io` are
different "registered domains"). Anything missing can be added at runtime through
`UrlPolicy.extra_public_suffixes` (env: `APP_URL_EXTRA_PUBLIC_SUFFIXES`).
"""

# ICANN-style multi-label suffixes (single-label TLDs need no entry).
MULTI_LABEL_SUFFIXES: frozenset[str] = frozenset(
    {
        # United Kingdom
        "co.uk", "org.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk", "ac.uk", "gov.uk", "sch.uk",
        # Australia / New Zealand
        "com.au", "net.au", "org.au", "edu.au", "gov.au", "asn.au", "id.au",
        "co.nz", "org.nz", "net.nz", "ac.nz", "govt.nz", "school.nz",
        # Japan / Korea / China / Hong Kong / Taiwan / Singapore
        "co.jp", "ne.jp", "or.jp", "ac.jp", "go.jp", "ed.jp",
        "co.kr", "or.kr", "ne.kr", "go.kr", "ac.kr",
        "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn",
        "com.hk", "org.hk", "edu.hk", "gov.hk", "net.hk",
        "com.tw", "org.tw", "edu.tw", "gov.tw", "net.tw",
        "com.sg", "org.sg", "edu.sg", "gov.sg", "net.sg",
        # India / South & South-East Asia
        "co.in", "net.in", "org.in", "ac.in", "gov.in", "edu.in", "firm.in", "gen.in", "ind.in",
        "com.my", "org.my", "edu.my", "gov.my", "net.my",
        "co.id", "or.id", "ac.id", "go.id", "web.id",
        "co.th", "in.th", "ac.th", "go.th",
        "com.ph", "org.ph", "edu.ph", "gov.ph", "net.ph",
        "com.vn", "net.vn", "org.vn", "edu.vn", "gov.vn",
        "com.pk", "org.pk", "edu.pk", "gov.pk", "net.pk",
        "com.bd", "org.bd", "edu.bd", "gov.bd", "net.bd",
        # Caucasus / Central Asia / Middle East
        "com.az", "org.az", "net.az", "edu.az", "gov.az",
        "com.ge", "org.ge", "edu.ge", "gov.ge",
        "co.il", "org.il", "ac.il", "gov.il", "net.il",
        "com.tr", "org.tr", "edu.tr", "gov.tr", "net.tr",
        "com.sa", "org.sa", "edu.sa", "gov.sa", "net.sa",
        "com.eg", "org.eg", "edu.eg", "gov.eg", "net.eg",
        # Africa
        "co.za", "org.za", "ac.za", "gov.za", "net.za",
        "co.ke", "or.ke", "ac.ke", "go.ke",
        "com.ng", "org.ng", "edu.ng", "gov.ng", "net.ng",
        # Americas
        "com.br", "net.br", "org.br", "gov.br", "edu.br",
        "com.ar", "org.ar", "gob.ar", "edu.ar", "net.ar",
        "com.mx", "org.mx", "gob.mx", "edu.mx", "net.mx",
        "com.co", "org.co", "edu.co", "gov.co", "net.co",
        "com.pe", "org.pe", "edu.pe", "gob.pe", "net.pe",
        "com.ve", "com.uy", "com.ec",
        # Europe / Eastern Europe
        "com.ua", "org.ua", "net.ua", "gov.ua", "edu.ua", "kiev.ua",
        "com.pl", "org.pl", "net.pl", "edu.pl", "gov.pl",
        "com.ru", "org.ru", "net.ru",
        "co.at", "or.at", "ac.at", "gv.at",
        "com.pt", "org.pt", "edu.pt", "gov.pt",
        "com.gr", "org.gr", "edu.gr", "gov.gr", "net.gr",
        "com.es", "org.es", "nom.es", "gob.es", "edu.es",
        "com.de", "com.fr", "asso.fr", "gouv.fr",
        "co.hu", "org.hu",
        "com.ro", "org.ro",
    }
)

# Platforms whose subdomains belong to unrelated tenants ("private" suffixes).
HOSTING_PLATFORM_SUFFIXES: frozenset[str] = frozenset(
    {
        "github.io", "gitlab.io", "herokuapp.com", "blogspot.com", "wordpress.com",
        "netlify.app", "vercel.app", "pages.dev", "workers.dev", "web.app",
        "firebaseapp.com", "azurewebsites.net", "cloudfront.net", "appspot.com",
        "myshopify.com", "wixsite.com", "weebly.com", "squarespace.com",
        "s3.amazonaws.com", "elasticbeanstalk.com", "onrender.com", "fly.dev",
        "glitch.me", "repl.co", "surge.sh", "ngrok.io", "ngrok-free.app",
        "tumblr.com", "webflow.io", "carrd.co", "notion.site", "readthedocs.io",
    }
)

ALL_BUILTIN_SUFFIXES: frozenset[str] = MULTI_LABEL_SUFFIXES | HOSTING_PLATFORM_SUFFIXES
