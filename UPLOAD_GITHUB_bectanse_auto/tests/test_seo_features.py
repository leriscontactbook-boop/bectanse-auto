import base64
import os


os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATA_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("ADMIN_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/test")
os.environ.setdefault("VAPID_PUBLIC_KEY", "test-public")
os.environ.setdefault("VAPID_PRIVATE_KEY", "test-private")
os.environ["BECTANSE_SKIP_STARTUP"] = "1"

import app as bectanse_app
from seo_features import INDEXNOW_KEY, indexable_pages


def test_public_seo_inventory_has_unique_canonical_paths():
    paths = [page["path"] for page in indexable_pages()]
    assert paths[0] == "/vip"
    assert "/guides" in paths
    assert "/guides/trading-or-xauusd" in paths
    assert len(paths) == len(set(paths))


def test_robots_and_sitemap_only_publish_acquisition_pages():
    client = bectanse_app.app.test_client()
    robots = client.get("/robots.txt")
    sitemap = client.get("/sitemap.xml")

    assert robots.status_code == 200
    assert "Sitemap: https://acces.bectanse-academie.com/sitemap.xml" in robots.text
    assert "Disallow: /admin" in robots.text
    assert sitemap.status_code == 200
    assert "https://acces.bectanse-academie.com/vip" in sitemap.text
    assert "https://acces.bectanse-academie.com/guides/trading-or-xauusd" in sitemap.text
    assert "/admin" not in sitemap.text


def test_public_guides_have_complete_search_metadata():
    client = bectanse_app.app.test_client()
    guide = client.get("/guides/trading-or-xauusd")

    assert guide.status_code == 200
    assert guide.headers["X-Robots-Tag"].startswith("index, follow")
    assert b'<link rel="canonical" href="https://acces.bectanse-academie.com/guides/trading-or-xauusd">' in guide.data
    assert b'application/ld+json' in guide.data
    assert b'og:image' in guide.data


def test_private_and_account_pages_are_not_indexable():
    client = bectanse_app.app.test_client()
    login = client.get("/")
    admin = client.get("/admin/seo")

    assert login.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"
    assert admin.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"


def test_vip_is_indexable_and_indexnow_key_is_public():
    client = bectanse_app.app.test_client()
    vip = client.get("/vip")
    key_file = client.get(f"/{INDEXNOW_KEY}.txt")

    assert vip.status_code == 200
    assert vip.headers["X-Robots-Tag"].startswith("index, follow")
    assert b'Bectanse Acad\xc3\xa9mie' in vip.data
    assert b'og:image' in vip.data
    assert key_file.status_code == 200
    assert key_file.text == INDEXNOW_KEY


def test_vip_testimonial_gallery_pins_and_maps_vertical_scroll_to_horizontal_slides():
    client = bectanse_app.app.test_client()
    vip = client.get("/vip")

    assert vip.status_code == 200
    html = vip.text
    assert 'id="proof-scroll"' in html
    assert "position:sticky" in html
    assert "overflow-x:clip" in html
    assert "touch-action:pan-y pinch-zoom" in html
    assert "translate3d('+translate+'%,0,0)" in html
    assert "proofScroll.offsetHeight-proofStage.offsetHeight" in html
    assert "data-active-proof" in html
    assert html.count('class="proof-photo-card') == 4
