import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"


def read(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_journal_uses_a_dedicated_three_zone_mobile_shell():
    template = read("templates/trading_journal.html")
    css = read("static/css/trading-journal.css")
    shell = template[template.index('<div class="tj-app">') : template.index('<dialog id="connectDialog"')]

    assert 'class="tj-mobile-header"' in shell
    assert 'class="tj-main"' in shell
    assert 'class="tj-mobile-nav"' in shell
    assert shell.index('class="tj-mobile-header"') < shell.index('class="tj-main"')
    assert shell.index('class="tj-main"') < shell.index('class="tj-mobile-nav"')
    assert "grid-template-rows: auto minmax(0, 1fr) auto" in css
    assert "height: var(--app-height)" in css
    assert ".tj-main::-webkit-scrollbar" in css
    assert "overscroll-behavior-y: contain" in css


def test_journal_mobile_navigation_is_stable_and_complete():
    template = read("templates/trading_journal.html")
    css = read("static/css/trading-journal.css")
    nav = template[template.index('<nav class="tj-mobile-nav"') : template.index("</nav>", template.index('<nav class="tj-mobile-nav"'))]

    assert nav.count("data-view=") == 5
    for view in ("overview", "trades", "calendar", "analytics", "coach"):
        assert f'data-view="{view}"' in nav
    assert ".tj-mobile-nav {\n    position: relative" in css
    assert "height: calc(var(--mobile-nav-height) + var(--safe-bottom))" in css
    assert "min-height: 44px" in css


def test_journal_has_safe_areas_deep_links_and_responsive_chart():
    template = read("templates/trading_journal.html")
    css = read("static/css/trading-journal.css")
    javascript = read("static/trading-journal.js")

    assert "viewport-fit=cover" in template
    for variable in ("--safe-top", "--safe-right", "--safe-bottom", "--safe-left", "--app-height"):
        assert variable in css
    assert "100dvh" in css
    assert "(max-width: 932px) and (max-height: 500px) and (orientation: landscape)" in css
    assert "viewFromLocation" in javascript
    assert "history.pushState" in javascript
    assert "aria-current" in javascript
    assert "ResizeObserver" in javascript
    assert "window.scrollTo" not in javascript
    assert "touchcancel" in javascript


def test_journal_mobile_sheets_lock_the_primary_scroller():
    css = read("static/css/trading-journal.css")
    javascript = read("static/trading-journal.js")

    assert "dialog.tj-dialog, dialog.tj-day-dialog" in css
    assert "max-height: calc(var(--app-height) - var(--safe-top) - 10px)" in css
    assert ".tj-main.is-scroll-locked" in css
    assert "function openDialog" in javascript
    assert "function unlockDialog" in javascript


def test_shared_member_navigation_does_not_follow_window_scroll():
    shared = read("templates/_mobile_nav.html")

    assert "--m-app-viewport-shift" not in shared
    assert "syncMobileViewport" not in shared
    assert "window.visualViewport" not in shared
    assert "var(--z-bottom-nav)" in shared
    assert "var(--safe-bottom)" in shared
    assert "lockedScrollY" in shared


def test_every_shared_mobile_member_route_has_viewport_fit_cover():
    routed_templates = []
    for template_path in TEMPLATES.glob("*.html"):
        source = template_path.read_text(encoding="utf-8")
        if '_mobile_nav.html' in source:
            routed_templates.append(template_path.name)
            assert "viewport-fit=cover" in source, template_path.name

    assert len(routed_templates) >= 20


def test_manifest_launches_canonical_home_in_any_orientation():
    manifest = json.loads(read("static/manifest.json"))

    assert manifest["start_url"] == "/accueil"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["orientation"] == "any"
    assert manifest["background_color"] == manifest["theme_color"] == "#050605"
    assert {icon["sizes"] for icon in manifest["icons"]} >= {"192x192", "512x512"}
