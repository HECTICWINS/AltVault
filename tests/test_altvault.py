"""AltVault's automated checks. Run from the repo root on Windows:

    py -3 -m unittest discover -s tests -v

They never touch your real data: LOCALAPPDATA is pointed at a temporary folder before AltVault is
imported, and nothing here talks to Roblox or any other server.
"""
import ast
import http.server
import importlib.util
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "altvault.py")
TEMP_HOME = tempfile.mkdtemp(prefix="altvault-test-")
os.environ["LOCALAPPDATA"] = TEMP_HOME

_spec = importlib.util.spec_from_file_location("altvault", SOURCE)
av = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(av)

FAKE_COOKIE = "_|WARNING:-DO-NOT-SHARE-THIS.--|_" + "T3stC00kie" * 20
FAKE_TICKET = "TICKET-" + "x" * 60


class TestDataStaysInTempFolder(unittest.TestCase):
    def test_app_dir_is_redirected(self):
        self.assertTrue(av.APP_DIR.startswith(TEMP_HOME), "tests must never touch the real data folder")


class TestCookieOnlyGoesToRoblox(unittest.TestCase):
    def test_roblox_url_check(self):
        good = ["https://www.roblox.com/", "https://auth.roblox.com/v1/x", "https://roblox.com", "https://WWW.ROBLOX.COM/a"]
        bad = ["http://www.roblox.com/", "https://evilroblox.com/", "https://roblox.com.evil.com/",
               "https://www.roblox.com@evil.com/", "https://evil.com/?u=https://www.roblox.com",
               "ftp://www.roblox.com/", "https://roblox.co/", "javascript:alert(1)", ""]
        for url in good:
            self.assertTrue(av._is_roblox_url(url), url)
        for url in bad:
            self.assertFalse(av._is_roblox_url(url), url)

    def test_cookie_refused_before_any_connection(self):
        with mock.patch.object(av._opener, "open", side_effect=AssertionError("connected")) as opened:
            for url in ("https://evil.com/", "http://www.roblox.com/", "https://evilroblox.com/"):
                with self.assertRaises(av.RobloxError):
                    av._request(url, FAKE_COOKIE)
            opened.assert_not_called()

    def test_redirect_away_from_roblox_drops_nothing_to_the_new_host(self):
        """A real redirect between two local servers: the second must never see the cookie."""
        seen = []

        class Target(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Cookie"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        target = http.server.HTTPServer(("127.0.0.1", 0), Target)
        target_url = f"http://127.0.0.1:{target.server_port}/steal"

        class Redirector(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", target_url)
                self.end_headers()

            def log_message(self, *a):
                pass

        start = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
        for server in (target, start):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{start.server_port}/",
                                         headers={"Cookie": ".ROBLOSECURITY=" + FAKE_COOKIE})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                av._opener.open(req, timeout=5)
            self.assertEqual(caught.exception.code, 302)
            caught.exception.close()
            self.assertEqual(seen, [], "the redirect target was contacted with the cookie")

            # Without a cookie the same redirect is followed normally.
            with av._opener.open(f"http://127.0.0.1:{start.server_port}/", timeout=5) as r:
                self.assertEqual(r.status, 200)
            self.assertEqual(seen, [None])
        finally:
            for server in (target, start):
                server.shutdown()
                server.server_close()

    def test_browser_login_only_takes_roblox_cookies(self):
        for domain in ("roblox.com", ".roblox.com", "www.roblox.com", ".WWW.Roblox.com"):
            self.assertTrue(av.is_roblox_cookie_domain(domain), domain)
        for domain in ("evilroblox.com", ".evilroblox.com", "roblox.com.evil.com", "", None, "com"):
            self.assertFalse(av.is_roblox_cookie_domain(domain), domain)

    def test_launch_link_only_points_at_roblox(self):
        uri = av.build_launch_uri(FAKE_TICKET, "123", "0a1b2c3d-1111-2222-3333-444455556666")
        launcher = urllib.parse.unquote(uri.split("placelauncherurl:")[1].split("+")[0])
        self.assertTrue(av._is_roblox_url(launcher), launcher)


class TestPrivateServers(unittest.TestCase):
    def test_link_detection(self):
        cases = {
            "https://www.roblox.com/games/920587237/Adopt-Me?privateServerLinkCode=1234567890abcDEF_-x":
                {"place_id": "920587237", "link_code": "1234567890abcDEF_-x"},
            "roblox.com/games/123/x?foo=1&privateServerLinkCode=ABCDEF":
                {"place_id": "123", "link_code": "ABCDEF"},
            "https://www.roblox.com/share?code=a1b2c3d4e5f6&type=Server": {"share_code": "a1b2c3d4e5f6"},
            "https://www.roblox.com/share?type=Server&code=a1b2c3d4e5f6": {"share_code": "a1b2c3d4e5f6"},
            "https://www.roblox.com/share?code=a1b2c3d4e5f6": {"share_code": "a1b2c3d4e5f6"},
            "https://www.roblox.com/privateServerLinkCode=1234?x": None,   # not a query parameter
        }
        for text, want in cases.items():
            self.assertEqual(av.parse_private_server(text), want, text)
        for text in ("https://www.roblox.com/games/920587237/Adopt-Me", "920587237", "",
                     "https://www.roblox.com/share?code=abcd1234&type=ExperienceDetails",
                     "https://evil.com/games/1/x?privateServerLinkCode=ABCDEF",
                     "https://notroblox.com/share?code=abcd1234&type=Server"):
            self.assertIsNone(av.parse_private_server(text), text)

    def test_private_link_is_not_a_public_game(self):
        """The old behaviour: pasting a private link joined a public server. It must now be private."""
        link = "https://www.roblox.com/games/920587237/Adopt-Me?privateServerLinkCode=ABCDEF123"
        self.assertEqual(av.parse_place_id(link), "920587237")       # still the right game...
        self.assertIsNotNone(av.parse_private_server(link))           # ...but recognised as private

    def test_launch_request_for_private_server(self):
        uri = av.build_launch_uri(FAKE_TICKET, "920587237", "ignored-job", "ABCDEF123")
        launcher = urllib.parse.unquote(uri.split("placelauncherurl:")[1].split("+")[0])
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(launcher).query)
        self.assertTrue(av._is_roblox_url(launcher))
        self.assertEqual(query["request"], ["RequestPrivateGame"])
        self.assertEqual(query["placeId"], ["920587237"])
        self.assertEqual(query["linkCode"], ["ABCDEF123"])
        self.assertNotIn("gameId", query)

    def test_public_requests_are_unchanged(self):
        for job, want in (("", "RequestGame"), ("0a1b2c3d-1111-2222-3333-444455556666", "RequestGameJob")):
            uri = av.build_launch_uri(FAKE_TICKET, "1", job)
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(
                urllib.parse.unquote(uri.split("placelauncherurl:")[1].split("+")[0])).query)
            self.assertEqual(query["request"], [want])
            self.assertNotIn("linkCode", query)

    def _resolve(self, status, body):
        calls = []

        def fake_post(url, cookie, payload=None, extra_headers=None):
            calls.append((url, payload))
            return status, {}, body
        with mock.patch.object(av, "_post", fake_post):
            try:
                return av.resolve_share_link(FAKE_COOKIE, "a1b2c3d4"), calls
            except av.RobloxError as e:
                return e, calls

    def test_share_link_resolves(self):
        body = b'{"privateServerInviteData": {"status": "Valid", "placeId": 920587237, "linkCode": "LC123"}}'
        result, calls = self._resolve(200, body)
        self.assertEqual(result, ("920587237", "LC123"))
        url, payload = calls[0]
        self.assertTrue(av._is_roblox_url(url))
        self.assertEqual(payload, {"linkId": "a1b2c3d4", "linkType": "Server"})

    def test_share_link_failures_are_clear_errors(self):
        bad = [(200, b'{"privateServerInviteData": {"status": "Expired", "placeId": 1, "linkCode": "x"}}'),
               (200, b'{"privateServerInviteData": {"status": "Valid", "placeId": null, "linkCode": "x"}}'),
               (200, b'{"privateServerInviteData": null}'), (200, b"not json"), (200, b"[]"),
               (400, b"{}"), (500, b"")]
        for status, body in bad:
            result, _ = self._resolve(status, body)
            self.assertIsInstance(result, av.RobloxError, body)
            self.assertNotIn("a1b2c3d4", str(result))

    def run_join(self, accounts, private, expired_cookies=()):
        launched, resolved, finished = [], [], []
        windows = []

        def fake_resolve(cookie, code):
            resolved.append(cookie)
            if cookie in expired_cookies:
                raise av.CookieExpired()
            return "920587237", "LC123"

        def fake_launch(cookie, place_id, job_id="", link_code=""):
            launched.append((cookie, place_id, job_id, link_code))
            windows.append((1000 + len(windows), 2000 + len(windows), "Roblox"))

        fake = types.SimpleNamespace(expired=set(), running={}, presence={}, settings={},
                                     set_status=lambda text: None, ui=lambda fn, *a: fn(*a),
                                     join_finished=finished.append, remember_place=lambda c, p: None,
                                     join_cancel=threading.Event(), pause_before_launch=lambda: True)
        for name in ("join_worker", "resolve_private", "wait_for_new_window", "fps_caps", "fps_cap_of",
                     "flagged", "client_overrides", "client_values_in_use", "client_state", "needs_client_settings", "apply_client_settings", "restore_client_settings"):
            setattr(fake, name, types.MethodType(getattr(av.App, name), fake))
        fake.who = lambda acc: "@" + acc["name"]
        fake.history = types.SimpleNamespace(add=lambda now=None, **k: None)
        fake.wait_for_server = lambda *a: self.fail("same-server logic ran for a private server")
        with mock.patch.object(av, "resolve_share_link", fake_resolve), \
                mock.patch.object(av, "launch", fake_launch), \
                mock.patch.object(av, "roblox_windows", lambda: list(windows)), \
                mock.patch.object(av.time, "sleep", lambda s: None), \
                mock.patch.object(av.threading, "Thread", lambda target, args=(), daemon=None:
                                  types.SimpleNamespace(start=lambda: target(*args))):
            fake.join_worker(accounts, None, "", True, private)
        return launched, resolved, finished[0], fake

    def test_whole_join_through_a_share_link(self):
        accounts = [{"id": i, "name": f"alt{i}", "cookie": f"cookie{i}"} for i in range(3)]
        launched, resolved, problems, _ = self.run_join(accounts, {"share_code": "a1b2c3d4"})
        self.assertEqual(resolved, ["cookie0"], "the link should be resolved once")
        self.assertEqual(launched, [(f"cookie{i}", "920587237", "", "LC123") for i in range(3)])
        self.assertEqual(problems, [])

    def test_share_link_falls_back_to_a_logged_in_account(self):
        accounts = [{"id": i, "name": f"alt{i}", "cookie": f"cookie{i}"} for i in range(2)]
        _, resolved, _, fake = self.run_join(accounts, {"share_code": "a1b2c3d4"}, expired_cookies={"cookie0"})
        self.assertEqual(resolved, ["cookie0", "cookie1"])
        self.assertIn(0, fake.expired)

    def test_old_style_link_needs_no_resolving(self):
        accounts = [{"id": 1, "name": "alt", "cookie": "c"}]
        launched, resolved, problems, _ = self.run_join(accounts, {"place_id": "55", "link_code": "OLD1"})
        self.assertEqual(resolved, [])
        self.assertEqual(launched, [("c", "55", "", "OLD1")])
        self.assertEqual(problems, [])

    def test_private_link_stays_out_of_plain_settings(self):
        saved = []
        fake = types.SimpleNamespace(
            settings={"place": "https://www.roblox.com/games/1/Public"},
            place_var=types.SimpleNamespace(get=lambda: "https://www.roblox.com/share?code=SECRET123&type=Server"),
            **{name: types.SimpleNamespace(get=lambda: True)
               for name in ("multi_var", "same_server_var", "auto_tile_var", "anti_afk_var", "stream_var",
                            "hide_taskbar_var")})
        with mock.patch.object(av, "save_settings", lambda s: saved.append(dict(s))):
            av.App.save_settings(fake)
        self.assertNotIn("SECRET123", repr(saved))
        self.assertEqual(saved[-1]["place"], "https://www.roblox.com/games/1/Public")
        self.assertTrue(saved[-1]["place_is_private"])

    def test_private_link_survives_a_restart_encrypted(self):
        link = "https://www.roblox.com/share?code=SECRET123&type=Server"
        self.addCleanup(lambda: os.path.exists(av.PRIVATE_FILE) and os.remove(av.PRIVATE_FILE))
        av.save_private_link(link)
        self.assertEqual(av.load_private_link(), link)
        with open(av.PRIVATE_FILE, "rb") as f:
            self.assertNotIn(b"SECRET123", f.read())

    def test_a_damaged_private_link_file_is_harmless(self):
        self.addCleanup(lambda: os.path.exists(av.PRIVATE_FILE) and os.remove(av.PRIVATE_FILE))
        os.makedirs(av.APP_DIR, exist_ok=True)
        with open(av.PRIVATE_FILE, "wb") as f:
            f.write(b"not encrypted")
        self.assertIsNone(av.load_private_link())


ROBLOX_SETTINGS_SAMPLE = (
    b'<roblox xmlns:xmime="http://www.w3.org/2005/05/xmlmime" version="4">\r\n'
    b'\t<Item class="UserGameSettings" referent="RBX0">\r\n\t\t<Properties>\r\n'
    b'\t\t\t<int name="FramerateCap">60</int>\r\n'
    b'\t\t\t<int name="GraphicsQualityLevel">3</int>\r\n'
    b'\t\t\t<float name="MasterVolume">0.5</float>\r\n'
    b'\t\t\t<int name="RCCProfilerRecordFrameRate">1</int>\r\n'
    b'\t\t</Properties>\r\n\t</Item>\r\n</roblox>')


class TestFpsCaps(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir=TEMP_HOME)
        self.path = os.path.join(self.folder, "GlobalBasicSettings_13.xml")
        with open(self.path, "wb") as f:
            f.write(ROBLOX_SETTINGS_SAMPLE)

    def contents(self):
        with open(self.path, "rb") as f:
            return f.read()

    def test_only_the_frame_rate_changes(self):
        self.assertEqual(av.read_fps_cap(self.path), 60)
        av.write_fps_cap(self.path, 20)
        self.assertEqual(av.read_fps_cap(self.path), 20)
        self.assertEqual(self.contents(), ROBLOX_SETTINGS_SAMPLE.replace(b">60<", b">20<"))
        self.assertIn(b'"RCCProfilerRecordFrameRate">1<', self.contents())   # similar name, untouched
        self.assertFalse(os.path.exists(self.path + ".altvault.tmp"))

    def test_file_without_the_setting_is_left_alone(self):
        with open(self.path, "wb") as f:
            f.write(b"<roblox></roblox>")
        with self.assertRaises(OSError):
            av.write_fps_cap(self.path, 20)
        self.assertEqual(self.contents(), b"<roblox></roblox>")
        self.assertIsNone(av.read_fps_cap(os.path.join(self.folder, "missing.xml")))

    def test_picks_the_player_settings_file(self):
        roblox = os.path.join(TEMP_HOME, "Roblox")
        os.makedirs(roblox, exist_ok=True)
        for name in ("GlobalBasicSettings_9.xml", "GlobalBasicSettings_13.xml",
                     "GlobalBasicSettings_14_Studio.xml", "GlobalSettings_20.xml"):
            open(os.path.join(roblox, name), "wb").close()
        self.assertEqual(os.path.basename(av.roblox_settings_file()), "GlobalBasicSettings_13.xml")

    def make_app(self, caps, **settings):
        fake = types.SimpleNamespace(settings={"fps_caps": caps, **settings}, ui=lambda fn, *a: fn(*a))
        for name in ("fps_caps", "fps_cap_of", "flagged", "client_overrides", "client_values_in_use", "client_state", "needs_client_settings", "apply_client_settings", "restore_client_settings"):
            setattr(fake, name, types.MethodType(getattr(av.App, name), fake))
        fake.who = lambda acc: "@" + acc["name"]
        fake.history = types.SimpleNamespace(add=lambda now=None, **k: None)
        return fake

    def test_each_account_starts_with_its_own_cap(self):
        app = self.make_app({"1": 20, "3": 45})
        seen = []
        with mock.patch.object(av, "save_settings", lambda s: None):
            for acc_id in (1, 2, 3):
                app.apply_client_settings(self.path, {"id": acc_id})
                seen.append(av.read_fps_cap(self.path))     # what this client would start with
                app.restore_client_settings(self.path)
                self.assertEqual(av.read_fps_cap(self.path), 60, "your own setting must come back")
        self.assertEqual(seen, [20, 60, 45])
        self.assertEqual(app.settings["client_own"]["FramerateCap"], "60")

    def test_a_closing_alt_cant_overwrite_your_own_setting(self):
        av.write_fps_cap(self.path, 20)          # an alt saved its cap when it closed
        app = self.make_app({"1": 20}, fps_own=60, fps_written=60)
        with mock.patch.object(av, "save_settings", lambda s: None):
            app.apply_client_settings(self.path, {"id": 2})
        self.assertEqual(app.settings["client_own"]["FramerateCap"], "60")
        self.assertEqual(av.read_fps_cap(self.path), 60)

    def test_your_in_game_change_is_respected(self):
        av.write_fps_cap(self.path, 144)         # you picked 144 in Roblox's menu
        app = self.make_app({"1": 20}, fps_own=60, fps_written=60)
        with mock.patch.object(av, "save_settings", lambda s: None):
            app.apply_client_settings(self.path, {"id": 1})
            self.assertEqual(av.read_fps_cap(self.path), 20)
            app.restore_client_settings(self.path)
        self.assertEqual(app.settings["client_own"]["FramerateCap"], "144")
        self.assertEqual(av.read_fps_cap(self.path), 144)

    def test_invalid_caps_are_ignored(self):
        app = self.make_app({"1": 20, "2": 0, "3": 9999, "4": "30", "5": None, "6": 240, "7": 5})
        self.assertEqual(app.fps_caps(), {"1": 20, "6": 240, "7": 5})
        self.assertEqual(self.make_app("garbage").fps_caps(), {})

    def use_full_sample(self):
        with open(self.path, "wb") as f:
            f.write(ROBLOX_SETTINGS_SAMPLE.replace(
                b'\t\t\t<int name="GraphicsQualityLevel">3</int>\r\n',
                b'\t\t\t<int name="GraphicsQualityLevel">3</int>\r\n\t\t\t<token name="SavedQualityLevel">6</token>\r\n'))
        return self.contents()

    def test_lowest_graphics_and_mute_per_account(self):
        original = self.use_full_sample()
        app = self.make_app({"2": 15}, low_graphics=[1, 2], muted=[2, 3])
        seen = {}
        with mock.patch.object(av, "save_settings", lambda s: None):
            for acc_id in (1, 2, 3, 4):
                app.apply_client_settings(self.path, {"id": acc_id})
                seen[acc_id] = av.read_client_settings(self.path)
                app.restore_client_settings(self.path)
                self.assertEqual(self.contents(), original, "every setting must go back exactly")
        own = {"FramerateCap": "60", "GraphicsQualityLevel": "3", "SavedQualityLevel": "6", "MasterVolume": "0.5"}
        self.assertEqual(seen[1], {**own, "SavedQualityLevel": "1", "GraphicsQualityLevel": "1"})
        self.assertEqual(seen[2], {"FramerateCap": "15", "SavedQualityLevel": "1", "GraphicsQualityLevel": "1",
                                   "MasterVolume": "0"})
        self.assertEqual(seen[3], {**own, "MasterVolume": "0"})
        self.assertEqual(seen[4], own)

    def test_a_closing_low_graphics_alt_cant_change_your_graphics(self):
        self.use_full_sample()
        app = self.make_app({}, low_graphics=[1], muted=[1])
        with mock.patch.object(av, "save_settings", lambda s: None):
            app.apply_client_settings(self.path, {"id": 2})     # learns your own values
            av.write_client_settings(self.path, {"SavedQualityLevel": "1", "GraphicsQualityLevel": "1",
                                                 "MasterVolume": "0"})   # the alt saved its own on close
            app.apply_client_settings(self.path, {"id": 2})
        self.assertEqual(av.read_client_settings(self.path)["SavedQualityLevel"], "6")
        self.assertEqual(av.read_client_settings(self.path)["MasterVolume"], "0.5")

    def test_only_numbers_are_written(self):
        original = self.use_full_sample()
        for bad in ("1<script>", "", "1e9", "-", "0x10"):
            with self.assertRaises(ValueError):
                av.write_client_settings(self.path, {"MasterVolume": bad})
        self.assertEqual(self.contents(), original)

    def test_join_applies_caps_in_order(self):
        events = []
        windows = []
        accounts = [{"id": i, "name": f"alt{i}", "cookie": f"cookie{i}"} for i in (1, 2, 3)]
        fake = types.SimpleNamespace(expired=set(), running={}, presence={}, ui=lambda fn, *a: fn(*a),
                                     settings={"fps_caps": {"1": 20, "3": 45}},
                                     set_status=lambda text: None, join_finished=lambda p: events.append(("done", p)),
                                     remember_place=lambda c, p: None, wait_for_server=lambda *a: None,
                                     join_cancel=threading.Event(), pause_before_launch=lambda: True)
        for name in ("join_worker", "wait_for_new_window", "fps_caps", "fps_cap_of",
                     "flagged", "client_overrides", "client_values_in_use", "client_state", "needs_client_settings", "apply_client_settings", "restore_client_settings"):
            setattr(fake, name, types.MethodType(getattr(av.App, name), fake))
        fake.who = lambda acc: "@" + acc["name"]
        fake.history = types.SimpleNamespace(add=lambda now=None, **k: None)

        def fake_launch(cookie, *a):
            events.append((cookie, av.read_fps_cap(self.path)))
            windows.append((len(windows), 500 + len(windows), "Roblox"))
        with mock.patch.object(av, "roblox_settings_file", lambda: self.path), \
                mock.patch.object(av, "launch", fake_launch), \
                mock.patch.object(av, "roblox_windows", lambda: list(windows)), \
                mock.patch.object(av, "save_settings", lambda s: None), \
                mock.patch.object(av.time, "sleep", lambda s: None), \
                mock.patch.object(av.threading, "Thread", lambda target, args=(), daemon=None:
                                  types.SimpleNamespace(start=lambda: target(*args))):
            fake.join_worker(accounts, "1", "", False)
        self.assertEqual(events, [("cookie1", 20), ("cookie2", 60), ("cookie3", 45), ("done", [])])
        self.assertEqual(av.read_fps_cap(self.path), 60)


class TestModes(unittest.TestCase):
    """Modes make alts save power and memory; the main account keeps your own settings."""

    OWN = {"FramerateCap": "60", "GraphicsQualityLevel": "3", "SavedQualityLevel": "6", "MasterVolume": "0.5"}
    ALT = {"SavedQualityLevel": "1", "GraphicsQualityLevel": "1", "MasterVolume": "0"}

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(dir=TEMP_HOME), "GlobalBasicSettings_13.xml")
        with open(self.path, "wb") as f:
            f.write(ROBLOX_SETTINGS_SAMPLE.replace(
                b'\t\t\t<int name="GraphicsQualityLevel">3</int>\r\n',
                b'\t\t\t<int name="GraphicsQualityLevel">3</int>\r\n\t\t\t<token name="SavedQualityLevel">6</token>\r\n'))
        self.original = self.contents()

    def contents(self):
        with open(self.path, "rb") as f:
            return f.read()

    def make_app(self, caps=None, **settings):
        return TestFpsCaps.make_app(self, caps or {}, **settings)

    def starts_with(self, app, ids):
        """What each account's client would start with; your own settings must come back after each."""
        seen = {}
        with mock.patch.object(av, "save_settings", lambda s: None):
            for acc_id in ids:
                app.apply_client_settings(self.path, {"id": acc_id})
                seen[acc_id] = av.read_client_settings(self.path)
                app.restore_client_settings(self.path)
                self.assertEqual(self.contents(), self.original, "every setting must go back exactly")
        return seen

    def test_grind_saves_on_alts_but_not_the_main_account(self):
        seen = self.starts_with(self.make_app({"3": 30}, mode="grind", main_account=1), (1, 2, 3))
        self.assertEqual(seen[1], self.OWN)
        self.assertEqual(seen[2], {**self.ALT, "FramerateCap": "15"})
        self.assertEqual(seen[3], {**self.ALT, "FramerateCap": "30"}, "an account's own cap wins over the mode's")

    def test_main_account_keeps_its_own_per_account_settings(self):
        seen = self.starts_with(self.make_app({"1": 45}, mode="max", main_account=1), (1, 2))
        self.assertEqual(seen[1], {**self.OWN, "FramerateCap": "45"})
        self.assertEqual(seen[2], {**self.ALT, "FramerateCap": "10"})

    def test_normal_mode_changes_nothing(self):
        seen = self.starts_with(self.make_app(mode="normal", main_account=1), (1, 2))
        self.assertEqual(seen, {1: self.OWN, 2: self.OWN})
        self.assertFalse(self.make_app(mode="normal").needs_client_settings())
        self.assertTrue(self.make_app(mode="grind").needs_client_settings())

    def test_a_closing_alt_cant_make_grind_values_your_own(self):
        app = self.make_app(mode="grind")
        self.starts_with(app, (2,))                              # learns your own values
        av.write_client_settings(self.path, {**self.ALT, "FramerateCap": "15"})   # the alt saved its own on close
        with mock.patch.object(av, "save_settings", lambda s: None):
            app.apply_client_settings(self.path, {"id": 3})
            app.restore_client_settings(self.path)
        self.assertEqual(self.contents(), self.original)

    def test_bad_values_mean_normal_and_no_main(self):
        for bad in (None, "turbo", 3, ["grind"], {"max": 1}):
            self.assertEqual(av.mode_of({"mode": bad}), "normal")
        for bad in (True, "1", 1.0, None):
            self.assertIsNone(av.main_account({"main_account": bad}))
        self.assertEqual(av.mode_rules({"mode": "max", "main_account": 7}, 7), {})
        self.assertEqual(av.mode_rules({"mode": "max", "main_account": 7}, 8)["fps"], 10)
        self.assertEqual(set(av.MODES), {key for key, _ in av.MODE_NAMES})
        self.assertEqual(set(av.MODES), set(av.MODE_HELP))

    def test_windows_is_only_asked_when_something_changes(self):
        calls = []
        fake = types.SimpleNamespace(settings={"mode": "max", "main_account": 1}, running={100: 1, 200: 2, 300: 3},
                                     saving={}, saving_refused=set())

        def fake_set(pid, eco, memory_low):
            calls.append((pid, eco, memory_low))
            return pid != 300                                  # Windows refuses this one
        with mock.patch.object(av, "set_process_saving", fake_set), mock.patch.object(av.log, "warning"):
            av.App.apply_modes(fake)
            self.assertEqual(calls, [(200, True, True), (300, True, True)], "the main account is left alone")
            av.App.apply_modes(fake)
            self.assertEqual(len(calls), 2, "settled, so no calls, and a refusal isn't retried every tick")
            fake.settings["mode"] = "normal"
            av.App.apply_modes(fake)
            self.assertEqual(calls[2:], [(200, False, False)], "back to normal priority")
            del fake.running[200]
            av.App.apply_modes(fake)
        self.assertEqual(fake.saving, {}, "closed windows are forgotten")


SERVER_ROOMY = "11111111-2222-3333-4444-555555555555"
SERVER_TIGHT = "66666666-7777-8888-9999-000000000000"


class TestJoinExtras(unittest.TestCase):
    """Stop, retry, a server with room for everyone, and waiting for memory."""

    def make(self, settings=None, launch=None):
        windows, launched = [], []
        fake = types.SimpleNamespace(expired=set(), running={}, presence={}, settings=settings or {},
                                     set_status=lambda text: None, ui=lambda fn, *a: fn(*a), join_cancel=threading.Event(),
                                     pause_before_launch=lambda: True, remember_place=lambda c, p: None)
        fake.finished = []
        fake.join_finished = fake.finished.append
        for name in ("join_worker", "wait_for_new_window", "fps_caps", "fps_cap_of", "flagged", "client_overrides",
                     "client_values_in_use", "client_state", "needs_client_settings", "apply_client_settings",
                     "restore_client_settings"):
            setattr(fake, name, types.MethodType(getattr(av.App, name), fake))
        fake.who = lambda acc: "@" + acc["name"]
        fake.login_age = lambda account_id: "a while"
        fake.history = types.SimpleNamespace(add=lambda now=None, **k: None)

        def fake_launch(cookie, place_id, job_id="", link_code=""):
            if launch:
                launch(fake, cookie)
            launched.append((cookie, job_id))
            windows.append((len(windows), 900 + len(windows), "Roblox"))
        return fake, launched, windows, fake_launch

    def run_join(self, fake, fake_launch, windows, accounts, same_server=False, servers=None):
        def get_servers(place_id, cursor=""):
            if isinstance(servers, Exception):
                raise servers
            return servers or [], ""
        with mock.patch.object(av, "launch", fake_launch), \
                mock.patch.object(av, "get_servers", get_servers), \
                mock.patch.object(av, "roblox_windows", lambda: list(windows)), \
                mock.patch.object(av.time, "sleep", lambda s: None), \
                mock.patch.object(av.threading, "Thread", lambda target, args=(), daemon=None:
                                  types.SimpleNamespace(start=lambda: target(*args))):
            fake.join_worker(accounts, "1818", "", same_server)
        return fake.finished[0]

    accounts = [{"id": i, "name": f"alt{i}", "cookie": f"cookie{i}"} for i in (1, 2, 3)]

    def test_everyone_goes_straight_to_a_server_with_room(self):
        fake, launched, windows, fake_launch = self.make()
        fake.wait_for_server = lambda *a: self.fail("nobody should wait for the first account")
        servers = [{"id": SERVER_TIGHT, "playing": 9, "maxPlayers": 10}, {"id": SERVER_ROOMY, "playing": 2, "maxPlayers": 10}]
        problems = self.run_join(fake, fake_launch, windows, self.accounts, same_server=True, servers=servers)
        self.assertEqual(problems, [])
        self.assertEqual(launched, [(f"cookie{i}", SERVER_ROOMY) for i in (1, 2, 3)])

    def test_without_a_server_list_the_others_follow_the_first(self):
        fake, launched, windows, fake_launch = self.make()
        followed = []
        fake.wait_for_server = lambda acc, place: followed.append(acc["id"]) or SERVER_ROOMY
        self.run_join(fake, fake_launch, windows, self.accounts, same_server=True, servers=av.RobloxError("429"))
        self.assertEqual(followed, [1])
        self.assertEqual([j for _, j in launched], ["", SERVER_ROOMY, SERVER_ROOMY])

    def test_stop_leaves_the_rest_ready_to_retry(self):
        fake, launched, windows, fake_launch = self.make(launch=lambda f, cookie: f.join_cancel.set())
        problems = self.run_join(fake, fake_launch, windows, self.accounts)
        self.assertEqual([c for c, _ in launched], ["cookie1"])
        self.assertIn("Stopped. 2 account(s) weren't launched.", problems)
        self.assertEqual(fake.join_failed, [2, 3])

    def test_a_failed_launch_is_offered_for_retry(self):
        def launch(f, cookie):
            if cookie == "cookie2":
                raise av.RobloxError("Roblox is down")
        fake, launched, windows, fake_launch = self.make(launch=launch)
        problems = self.run_join(fake, fake_launch, windows, self.accounts)
        self.assertEqual([c for c, _ in launched], ["cookie1", "cookie3"])
        self.assertEqual(fake.join_failed, [2])
        self.assertTrue(any("Roblox is down" in p for p in problems))

    def pause(self, settings, memory, waits):
        """Runs pause_before_launch with fake memory readings; `waits` gets each wait's seconds."""
        readings = iter(memory)
        cancel = types.SimpleNamespace(wait=lambda s: waits.append(s) or False, is_set=lambda: False)
        statuses = []
        fake = types.SimpleNamespace(settings=settings, join_cancel=cancel, set_status=statuses.append)
        with mock.patch.object(av, "memory_used_fraction", lambda: next(readings, 0.5)):
            return av.App.pause_before_launch(fake), statuses

    def test_waits_for_memory_then_launches(self):
        waits = []
        ok, statuses = self.pause({}, [0.95, 0.93, 0.6], waits)
        self.assertTrue(ok)
        self.assertEqual(waits, [2, 2])
        self.assertIn("95% used", statuses[0])

    def test_gap_between_launches_uses_allowed_values_only(self):
        waits = []
        self.pause({"launch_delay": 10, "launch_memory_wait": False}, [], waits)
        self.assertEqual(waits, [10])
        waits.clear()
        self.pause({"launch_delay": 7, "launch_memory_wait": False}, [], waits)
        self.assertEqual(waits, [], "7 isn't one of the choices, so it's ignored")

    def test_stopping_during_the_wait_stops_the_join(self):
        cancel = types.SimpleNamespace(wait=lambda s: True, is_set=lambda: True)
        fake = types.SimpleNamespace(settings={"launch_delay": 5}, join_cancel=cancel, set_status=lambda t: None)
        self.assertFalse(av.App.pause_before_launch(fake))


class TestLighterRendering(unittest.TestCase):
    """Allowed rendering flags for alts: added just before launch, the user's file always put back."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(dir=TEMP_HOME)
        self.path = os.path.join(self.folder, "ClientSettings", "ClientAppSettings.json")
        backup = os.path.join(tempfile.mkdtemp(dir=TEMP_HOME), "client-flags-backup.json")
        patcher = mock.patch.object(av, "FLAGS_BACKUP", backup)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.backup = backup

    def contents(self):
        with open(self.path, "rb") as f:
            return f.read()

    def test_flags_go_in_and_your_file_comes_back_byte_for_byte(self):
        os.makedirs(os.path.dirname(self.path))
        mine = b'\xef\xbb\xbf{"FFlagMyOwnThing": "True"}'
        with open(self.path, "wb") as f:
            f.write(mine)
        av.write_flags(self.folder, av.LOW_FLAGS)
        written = av.json.loads(self.contents())
        self.assertEqual(written["FFlagMyOwnThing"], "True", "your own flags stay while the alt starts")
        self.assertEqual(written["DFIntTextureQualityOverride"], "0")
        av.restore_flags()
        self.assertEqual(self.contents(), mine)
        self.assertFalse(os.path.exists(self.backup))

    def test_a_file_that_wasnt_there_is_removed_again(self):
        av.write_flags(self.folder, av.LOW_FLAGS)
        self.assertTrue(os.path.exists(self.path))
        av.restore_flags()
        self.assertFalse(os.path.exists(self.path))
        av.restore_flags()   # nothing pending: does nothing

    def test_a_damaged_file_is_left_alone(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "wb") as f:
            f.write(b"[1, 2]")
        with self.assertRaises(ValueError):
            av.write_flags(self.folder, av.LOW_FLAGS)
        self.assertEqual(self.contents(), b"[1, 2]")
        self.assertFalse(os.path.exists(self.backup))

    def test_a_backup_never_touches_another_file(self):
        other = os.path.join(self.folder, "important.txt")
        with open(other, "w") as f:
            f.write("keep me")
        with open(self.backup, "w") as f:
            av.json.dump({"path": other, "old": None}, f)
        av.restore_flags()
        self.assertTrue(os.path.exists(other))
        self.assertFalse(os.path.exists(self.backup))

    def test_only_alts_on_lowest_graphics_get_them(self):
        self.assertTrue(av.wants_low_flags({"mode": "grind", "main_account": 1}, 2))
        self.assertFalse(av.wants_low_flags({"mode": "grind", "main_account": 1}, 1), "the main account keeps full quality")
        self.assertFalse(av.wants_low_flags({"mode": "normal"}, 2))
        self.assertTrue(av.wants_low_flags({"low_graphics": [5]}, 5))
        self.assertFalse(av.wants_low_flags({"mode": "max", "lighter_rendering": False}, 2))

    def test_player_folder_only_when_roblox_starts_directly(self):
        player = os.path.join(self.folder, "RobloxPlayerBeta.exe")
        open(player, "w").close()
        key = mock.MagicMock()
        for command, expected in ((f'"{player}" %1', self.folder), (r'"C:\Bloxstrap\Bloxstrap.exe" -player "%1"', None)):
            with mock.patch.object(av.winreg, "OpenKey", lambda *a: key), mock.patch.object(av.winreg, "QueryValue", lambda *a: command):
                self.assertEqual(av.roblox_player_folder(), expected)
        with mock.patch.object(av.winreg, "OpenKey", mock.Mock(side_effect=OSError)):
            self.assertIsNone(av.roblox_player_folder())

    def test_join_gives_flags_to_alts_only(self):
        seen = []
        settings_file = os.path.join(self.folder, "GlobalBasicSettings_13.xml")
        with open(settings_file, "wb") as f:
            f.write(ROBLOX_SETTINGS_SAMPLE)
        fake, launched, windows, fake_launch = TestJoinExtras.make(self, settings={"mode": "grind", "main_account": 1},
                                                                    launch=lambda f, c: seen.append(os.path.exists(self.path)))
        accounts = [{"id": i, "name": f"alt{i}", "cookie": f"cookie{i}"} for i in (1, 2)]
        with mock.patch.object(av, "roblox_player_folder", lambda: self.folder), \
                mock.patch.object(av, "roblox_settings_file", lambda: settings_file), \
                mock.patch.object(av, "save_settings", lambda s: None):
            TestJoinExtras.run_join(self, fake, fake_launch, windows, accounts)
        self.assertEqual(seen, [False, True], "main starts without the flags, the alt with them")
        self.assertFalse(os.path.exists(self.path), "and they're gone again afterwards")


class TestUpkeep(unittest.TestCase):
    def test_old_roblox_logs_are_deleted_and_new_ones_kept(self):
        logs = os.path.join(TEMP_HOME, "Roblox", "logs")
        os.makedirs(logs, exist_ok=True)
        now = time.time()
        for name, age_days in (("old.log", 5), ("new.log", 1), ("old-notes.txt", 9)):
            path = os.path.join(logs, name)
            with open(path, "w") as f:
                f.write("x" * 100)
            os.utime(path, (now - age_days * 86400, now - age_days * 86400))
        self.assertEqual(av.clean_roblox_logs(now=now), (1, 100))
        self.assertEqual(sorted(os.listdir(logs)), ["new.log", "old-notes.txt"])

    def test_update_check_reads_only_our_releases(self):
        def reply(status, body):
            return lambda *a, **k: (status, {}, av.json.dumps(body).encode())
        good = {"tag_name": "v2.9.1", "html_url": av.GITHUB_URL + "/releases/tag/v2.9.1"}
        with mock.patch.object(av, "_request", reply(200, good)):
            self.assertEqual(av.latest_release(), ((2, 9, 1), good["html_url"]))
        with mock.patch.object(av, "_request", reply(200, {**good, "html_url": "https://evil.example/download"})):
            self.assertIsNone(av.latest_release())
        with mock.patch.object(av, "_request", reply(404, {})):
            self.assertIsNone(av.latest_release())
        self.assertEqual(av.version_tuple("2.10.0"), (2, 10, 0))
        self.assertGreater(av.version_tuple("v2.10.0"), av.version_tuple(av.APP_VERSION))
        self.assertIsNone(av.version_tuple("latest"))


WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/" + "AbC-def_" * 8


class TestDiscordAlerts(unittest.TestCase):
    def setUp(self):
        self.addCleanup(lambda: av.save_webhook(None))

    def test_only_discord_webhook_links_are_accepted(self):
        for good in (WEBHOOK, WEBHOOK.replace("discord.com", "ptb.discord.com"), WEBHOOK.replace("discord.com", "discordapp.com")):
            self.assertTrue(av.WEBHOOK_RE.fullmatch(good), good)
        for bad in ("http://discord.com/api/webhooks/1234567/" + "a" * 30, "https://discord.com.evil.io/api/webhooks/1234567/" + "a" * 30,
                    "https://evil.io/?https://discord.com/api/webhooks/1234567/" + "a" * 30, WEBHOOK + "/../x", ""):
            self.assertFalse(av.WEBHOOK_RE.fullmatch(bad), bad)
            with self.assertRaises(ValueError):
                av.save_webhook(bad)

    def test_saved_encrypted_and_removable(self):
        av.save_webhook(WEBHOOK)
        with open(av.WEBHOOK_FILE, "rb") as f:
            self.assertNotIn(b"webhooks", f.read())
        self.assertEqual(av.load_webhook(), WEBHOOK)
        av.save_webhook(None)
        self.assertIsNone(av.load_webhook())

    def test_the_post_holds_only_the_alert(self):
        sent = []
        with mock.patch.object(av, "_request", lambda url, cookie=None, method="GET", headers=None, data=None:
                               sent.append((url, cookie, method, av.json.loads(data))) or (204, {}, b"")):
            self.assertTrue(av.send_webhook(WEBHOOK, "@alt2: idle kick", "Open Doctor."))
            self.assertFalse(av.send_webhook("https://evil.io/x", "t", "x"))
        url, cookie, method, payload = sent[0]
        self.assertEqual((url, cookie, method), (WEBHOOK, None, "POST"))
        self.assertEqual(payload["allowed_mentions"], {"parse": []}, "a name can't ping anyone")
        self.assertEqual(payload["content"], "**@alt2: idle kick**\nOpen Doctor.")
        self.assertEqual(len(sent), 1)

    def test_a_bad_night_cant_spam_discord(self):
        posted = []
        fake = types.SimpleNamespace(webhook=WEBHOOK, webhook_sent=[], post_webhook=lambda *a: posted.append(a))
        with mock.patch.object(av.threading, "Thread", lambda target, args=(), daemon=None:
                               types.SimpleNamespace(start=lambda: target(*args))):
            for i in range(30):
                av.App.send_alert(fake, f"alert {i}", "")
        self.assertEqual(len(posted), av.WEBHOOK_PER_HOUR)
        fake.webhook, posted[:] = None, []
        av.App.send_alert(types.SimpleNamespace(webhook=None, webhook_sent=[]), "t", "")
        self.assertEqual(posted, [])


class TestHotkey(unittest.TestCase):
    def test_pressed_key_calls_back_and_stops_cleanly(self):
        pressed = threading.Event()
        key = av.Hotkey(av.MOD_CONTROL | av.MOD_ALT | 0x0004, 0x87, pressed.set)   # Ctrl+Alt+Shift+F24: never in use
        self.assertTrue(key.registered)
        av._user32.PostThreadMessageW(key.thread_id, av.WM_HOTKEY, 1, 0)   # what Windows sends on the key press
        self.assertTrue(pressed.wait(2))
        key.stop()
        key.thread.join(2)
        self.assertFalse(key.thread.is_alive(), "its thread ends, so nothing is left running")


class TestNewInterface(unittest.TestCase):
    """The state the page gets: everything it draws, and nothing it shouldn't have."""

    def test_state_for_the_page(self):
        accounts = [{"id": 1, "name": "mainacc", "displayName": "Hectic", "cookie": FAKE_COOKIE, "note": ""},
                    {"id": 2, "name": "farmalt1", "displayName": "Farm One", "cookie": FAKE_COOKIE, "note": "x", "group": "Farm"}]
        fake = types.SimpleNamespace(accounts=accounts, settings={"mode": "grind", "main_account": 1}, running={77: 2},
                                     presence={2: {"userPresenceType": 2, "lastLocation": "Pet Sim"}}, expired=set(),
                                     avatar_px=48, joining=False, stream_var=types.SimpleNamespace(get=lambda: False),
                                     anti_afk_var=types.SimpleNamespace(get=lambda: True), place_var=types.SimpleNamespace(get=lambda: "1818"),
                                     status=types.SimpleNamespace(cget=lambda k: "Done."))
        for name in ("fps_caps", "fps_cap_of", "flagged", "group_of", "who"):
            setattr(fake, name, types.MethodType(getattr(av.App, name), fake))
        state = av.App.web_state(fake)
        main, alt = state["accounts"]
        self.assertEqual((main["perf"], main["main"], main["status"]), ("Full quality", True, "—"))
        self.assertEqual((alt["status"], alt["kind"], alt["game"]), ("In game", "ingame", "Pet Sim"))
        self.assertEqual(alt["perf"], "15 FPS · low graphics · muted")
        self.assertEqual((state["open"], state["ingame"], state["mode"]), (1, 1, "grind"))
        self.assertNotIn(FAKE_COOKIE, av.json.dumps(state), "logins never reach the page")

    def test_page_files_are_local_and_locked_down(self):
        page = open(os.path.join(ROOT, "ui", "index.html"), encoding="utf-8").read()
        self.assertIn("default-src 'self'", page)
        script = open(os.path.join(ROOT, "ui", "app.js"), encoding="utf-8").read()
        for remote in ("fetch(", "XMLHttpRequest", "http://", "eval(", "innerHTML = msg"):
            self.assertNotIn(remote, script)


class TestProcessSaving(unittest.TestCase):
    """Efficiency mode, CPU and memory priority, tried on a throwaway process of our own (never Roblox)."""

    def state(self, pid):
        k32, wt = av._kernel32, av.wt
        k32.GetPriorityClass.argtypes = [wt.HANDLE]
        k32.GetProcessInformation.argtypes = [wt.HANDLE, av.ctypes.c_int, av.ctypes.c_void_p, wt.DWORD]
        handle = k32.OpenProcess(av.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        self.assertTrue(handle)
        try:
            memory, throttle = wt.ULONG(), av.POWER_THROTTLING(1, 0, 0)
            self.assertTrue(k32.GetProcessInformation(handle, av.PROCESS_MEMORY_PRIORITY, av.ctypes.byref(memory), 4))
            self.assertTrue(k32.GetProcessInformation(handle, av.PROCESS_POWER_THROTTLING, av.ctypes.byref(throttle), 12))
            return k32.GetPriorityClass(handle), memory.value, throttle.StateMask
        finally:
            k32.CloseHandle(handle)

    def test_on_and_back_to_normal(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        normal = (av.NORMAL_PRIORITY_CLASS, av.MEMORY_PRIORITY_NORMAL, 0)
        # no check of the starting state: a child inherits its parent's priority, which CI lowers
        self.assertTrue(av.set_process_saving(child.pid, eco=True))
        self.assertEqual(self.state(child.pid), (av.BELOW_NORMAL_PRIORITY_CLASS, av.MEMORY_PRIORITY_NORMAL, 1))
        self.assertTrue(av.set_process_saving(child.pid, eco=True, memory_low=True))
        self.assertEqual(self.state(child.pid), (av.BELOW_NORMAL_PRIORITY_CLASS, av.MEMORY_PRIORITY_LOW, 1))
        self.assertTrue(av.set_process_saving(child.pid))
        self.assertEqual(self.state(child.pid), normal)

    def test_a_process_windows_protects_is_refused_quietly(self):
        self.assertFalse(av.set_process_saving(4, eco=True))     # the System process


class TestSecretsNeverLeak(unittest.TestCase):
    SECRET_NAMES = {"cookie", "ticket", "uri", "link_code", "share_code", "private"}

    @classmethod
    def setUpClass(cls):
        with open(SOURCE, encoding="utf-8") as f:
            cls.tree = ast.parse(f.read())

    def _secret_refs(self, node):
        found = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id in self.SECRET_NAMES:
                found.append(sub.id)
            elif isinstance(sub, ast.Attribute) and sub.attr in self.SECRET_NAMES:
                found.append(sub.attr)
            elif isinstance(sub, ast.Subscript) and isinstance(sub.slice, ast.Constant) \
                    and sub.slice.value in self.SECRET_NAMES:
                found.append(sub.slice.value)
        return found

    def test_no_log_call_mentions_a_secret(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "log":
                refs = [r for a in node.args + [k.value for k in node.keywords] for r in self._secret_refs(a)]
                self.assertEqual(refs, [], f"log call on line {node.lineno} uses {refs}")

    def test_no_error_message_includes_a_secret(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in ("RobloxError", "CookieExpired"):
                refs = [r for a in node.args for r in self._secret_refs(a)]
                self.assertEqual(refs, [], f"error on line {node.lineno} uses {refs}")

    def test_no_print_calls(self):
        prints = [n.lineno for n in ast.walk(self.tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"]
        self.assertEqual(prints, [])

    def test_failed_launch_hides_the_ticket(self):
        err = OSError(2, f"cannot open roblox-player:1+gameinfo:{FAKE_TICKET}")
        err.winerror = 2
        with mock.patch.object(av, "get_auth_ticket", return_value=FAKE_TICKET), \
                mock.patch.object(av.os, "startfile", side_effect=err, create=True):
            with self.assertRaises(av.RobloxError) as caught:
                av.launch(FAKE_COOKIE, "123")
        self.assertNotIn(FAKE_TICKET, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_network_error_hides_the_request(self):
        with mock.patch.object(av._opener, "open", side_effect=urllib.error.URLError("boom")):
            with self.assertRaises(av.RobloxError) as caught:
                av._request("https://users.roblox.com/v1/users/authenticated", FAKE_COOKIE)
        self.assertNotIn(FAKE_COOKIE, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_cookie_format_check(self):
        self.assertTrue(av.COOKIE_RE.fullmatch(FAKE_COOKIE))
        for bad in (FAKE_COOKIE + "\n", FAKE_COOKIE[:30] + " " + FAKE_COOKIE[30:], "short", FAKE_COOKIE + "\r\nX: y"):
            self.assertIsNone(av.COOKIE_RE.fullmatch(bad), repr(bad[-12:]))


class TestStoredAccounts(unittest.TestCase):
    def setUp(self):
        self.accounts = [{"id": 1, "name": "test_user", "displayName": "Test", "cookie": FAKE_COOKIE, "note": ""}]
        av.save_accounts(self.accounts)

    def test_round_trip(self):
        self.assertEqual(av.load_accounts(), self.accounts)

    def test_file_is_encrypted(self):
        with open(av.DATA_FILE, "rb") as f:
            raw = f.read()
        self.assertNotIn(FAKE_COOKIE.encode(), raw)
        self.assertNotIn(b"test_user", raw)

    def test_tampered_file_is_rejected(self):
        with open(av.DATA_FILE, "rb") as f:
            raw = bytearray(f.read())
        raw[len(raw) // 2] ^= 0xFF
        with self.assertRaises(OSError):
            av.unprotect(bytes(raw))

    def test_file_is_locked_to_this_user(self):
        acl = subprocess.run(["icacls", av.DATA_FILE], capture_output=True, text=True).stdout
        for group in ("Everyone", "BUILTIN\\Users", "Authenticated Users"):
            self.assertNotIn(group, acl)
        self.assertNotIn("(I)", acl, "inherited permissions should have been removed")


class TestParsing(unittest.TestCase):
    def test_place_ids(self):
        cases = {"920587237": "920587237", "https://www.roblox.com/games/920587237/Adopt-Me": "920587237",
                 "https://www.roblox.com/en/games/123/x": "123", "roblox.com/games/start?placeId=55": "55",
                 "Tap Simulator  (72858262610964)": "72858262610964", "hello": None, "": None}
        for text, want in cases.items():
            self.assertEqual(av.parse_place_id(text), want, text)

    def test_job_ids(self):
        u = "0a1b2c3d-1111-2222-3333-444455556666"
        self.assertEqual(av.parse_job_id(""), "")
        self.assertEqual(av.parse_job_id(u), u)
        self.assertEqual(av.parse_job_id("https://x/?gameInstanceId=" + u), u)
        self.assertIsNone(av.parse_job_id("not a server"))


class TestAntiAfkTiming(unittest.TestCase):
    """Simulates 12 hours of Anti-AFK with four clients, a user who's often busy, a window that
    misses a quarter of its pings, and a client that closes while another opens. Roblox kicks at
    20 minutes; no window may come close."""

    def simulate(self, seed, flaky=0.25, busy_user=0.6, hours=12):
        rnd = random.Random(seed)
        clock = [0.0]
        windows = {101: 1, 102: 2, 103: 3, 104: 4}
        last = {h: 0.0 for h in windows}
        worst = {h: 0.0 for h in windows}

        def fake_pass(hwnds, vk, strong=False):
            pinged, missed, reasons = [], [], {}
            for h in hwnds:
                if h == 103 and rnd.random() < flaky:
                    missed.append(h)
                    reasons[h] = "not responding"
                else:
                    pinged.append(h)
                    worst[h] = max(worst[h], clock[0] - last[h])
                    last[h] = clock[0]
            clock[0] += 0.7 * len(hwnds)
            return pinged, [], missed, reasons, ("lockapp.exe" if missed else None)

        timer = [None]
        fake = types.SimpleNamespace(
            root=types.SimpleNamespace(after=lambda ms, fn: timer.__setitem__(0, (clock[0] + ms / 1000, fn)) or 1,
                                       after_cancel=lambda job: timer.__setitem__(0, None)),
            anti_afk_var=types.SimpleNamespace(get=lambda: True), settings={}, running={}, joining=False,
            afk_busy=False, afk_busy_since=0, afk_pass_id=0, afk_wait_started=None, afk_waiting_for=None,
            afk_waiting_since=0, afk_waiting_logged=False, afk_last={}, afk_misses={}, afk_job=None, afk_alerted=False, tray=None,
            status=types.SimpleNamespace(config=lambda **k: None), account=lambda i: None)
        for name in ("schedule_afk", "afk_skip_ids", "afk_due", "_afk_due", "_afk_waiting",
                     "afk_finished", "afk_next_delay", "afk_window_name", "afk_alert"):
            setattr(fake, name, types.MethodType(getattr(av.App, name), fake))
        fake.ui = lambda fn, *a: fn(*a)
        fake.history = av.History(os.path.join(av.APP_DIR, "history-test.json"))

        user32 = types.SimpleNamespace(IsIconic=lambda h: False, GetWindowThreadProcessId=lambda h, p: None)
        with mock.patch.object(av.time, "monotonic", lambda: clock[0]), \
                mock.patch.object(av, "roblox_windows", lambda: [(h, p, "Roblox") for h, p in windows.items()]), \
                mock.patch.object(av, "idle_seconds", lambda: 0.0 if rnd.random() < busy_user else 600.0), \
                mock.patch.object(av, "_user32", user32), \
                mock.patch.object(av, "anti_afk_pass", fake_pass), \
                mock.patch.object(av.random, "uniform", rnd.uniform), \
                mock.patch.object(av.threading, "Thread", lambda target, daemon: types.SimpleNamespace(start=target)), \
                mock.patch.object(av.log, "info"), mock.patch.object(av.log, "warning"):
            fake.schedule_afk(3)
            end, swapped = hours * 3600, False
            while clock[0] < end:
                due, fn = timer[0]
                clock[0] = max(clock[0], due)
                fn()
                if not swapped and clock[0] > end / 2:
                    windows.pop(104)
                    windows[105] = 5
                    last[105], worst[105], swapped = clock[0], 0.0, True
        for h in windows:
            worst[h] = max(worst[h], clock[0] - last[h])
        return worst

    def test_no_window_gets_near_the_idle_kick(self):
        healthy = flaky = 0.0
        for seed in range(30):
            worst = self.simulate(seed)
            flaky = max(flaky, worst[103])
            healthy = max(healthy, max(v for h, v in worst.items() if h != 103))
        self.assertLess(healthy / 60, 15.5, "a healthy window went too long without a ping")
        self.assertLess(flaky / 60, 17, "a window that often misses went too long without a ping")

    def test_pc_in_use_is_disturbed_about_half_as_often(self):
        worst = self.simulate(1, flaky=0, busy_user=1)
        self.assertGreater(max(worst.values()) / 60, 11.9)
        self.assertLess(max(worst.values()) / 60, 15.5)

    def test_calm_conditions_ping_on_schedule(self):
        worst = self.simulate(1, flaky=0, busy_user=0)
        self.assertLess(max(worst.values()) / 60, 9.5)


class TestStayConnected(unittest.TestCase):
    def fake(self):
        app = types.SimpleNamespace(presence={}, last_ingame={}, first_seen={}, afk_skipped=lambda acc: False)
        app.seconds_out_of_game = lambda acc, pid, now=None: av.App.seconds_out_of_game(app, acc, pid, now)
        return app

    def test_a_normal_rejoin_is_never_flagged(self):
        app, acc = self.fake(), {"id": "a"}
        app.last_ingame["a"] = 1000.0
        self.assertFalse(av.App.is_lost(app, acc, 1) and app.seconds_out_of_game(acc, 1, 1060.0) > av.LOST_AFTER)
        self.assertLess(app.seconds_out_of_game(acc, 1, 1060.0), av.LOST_AFTER)

    def test_long_outside_a_game_counts_and_in_game_does_not(self):
        app, acc = self.fake(), {"id": "a"}
        app.last_ingame["a"] = 1000.0
        app.first_seen[1] = 0.0
        self.assertGreater(app.seconds_out_of_game(acc, 1, 1000.0 + av.LOST_AFTER + 5), av.LOST_AFTER)
        app.presence["a"] = {"userPresenceType": 2}
        self.assertEqual(app.seconds_out_of_game(acc, 1, 99999.0), 0.0)

    def test_accounts_with_anti_afk_off_are_not_flagged(self):
        app, acc = self.fake(), {"id": "a"}
        app.afk_skipped = lambda a: True
        app.first_seen[1] = 0.0
        self.assertFalse(av.App.is_lost(app, acc, 1))

    def test_every_ping_is_exactly_one_short_tap(self):
        """A second tap or a long hold double jumps, which pulls a character off an egg."""
        taps, waits = [], []
        with mock.patch.object(av.time, "sleep", waits.append), \
                mock.patch.object(av, "tap_key", lambda vk, *a: taps.append(a)), \
                mock.patch.object(av._user32, "GetForegroundWindow", lambda: 7):
            for strong in (False, True):
                taps.clear()
                self.assertTrue(av.press_when_ready(7, 0x20, strong))
                self.assertEqual(taps, [()], "one tap, at the normal length")
            self.assertIn(av.AFK_SETTLE_SLOW, waits, "memory pressure only waits longer first")
            self.assertFalse(av.press_when_ready(8, 0x20), "never press into a window that isn't in front")
        self.assertLess(av.AFK_HOLD, 0.25)


class TestLaunchTicket(unittest.TestCase):
    def run_ticket(self, replies, user_ok=True):
        calls = iter(replies)
        users = mock.Mock(side_effect=None if user_ok else av.CookieExpired())
        with mock.patch.object(av, "_post", lambda *a, **k: next(calls)), mock.patch.object(av, "get_user", users), \
                mock.patch.object(av.time, "sleep"):
            return av.get_auth_ticket("c" * 60)

    def test_a_dead_login_is_reported_as_expired(self):
        with self.assertRaises(av.CookieExpired):
            self.run_ticket([(401, {}, b"{}")], user_ok=False)

    def test_a_working_login_is_not_called_expired_and_is_retried(self):
        ok = (200, {"rbx-authentication-ticket": "T"}, b"")
        self.assertEqual(self.run_ticket([(401, {}, b"no"), ok]), "T")

    def test_still_refused_gives_a_plain_error_not_expired(self):
        with self.assertRaises(av.RobloxError) as cm:
            self.run_ticket([(401, {}, b"no"), (401, {}, b"no")])
        self.assertNotIsInstance(cm.exception, av.CookieExpired)


def roblox_line(when, text):
    return f"{when}Z,12.5,27b8,6 {text}\n".encode()


JOIN = "[FLog::Output] ! Joining game '{}' place 728 at 10.60.1.34"
A, B = "3e8ae2c7-a54b-4aaa-a217-479e51ee0ec0", "8b4f3378-a54b-4aaa-a217-479e51ee0ec1"
LOADED = ("[FLog::GameJoinLoadTime] Report game_join_loadtime: placeid:728, join_time:0.9, universeid:1, "
          "referral_page:RequestGame, sid:daa6, clienttime:1793406922.4260001183, userid:{},")
DROP = "[FLog::Network] Sending disconnect with reason: {}"
LOW = "[FLog::SingleSurfaceApp] memoryPrioritizationCallback (level:3)"


class TestDoctorLogs(unittest.TestCase):
    def log(self, *lines):
        return b"".join(roblox_line(*line) for line in lines)

    def test_an_idle_kick_names_the_account_once(self):
        state, data = {}, self.log(("2026-10-27T15:00:00.100", JOIN.format(A)), ("2026-10-27T15:00:00.100", LOADED.format(77)),
                                   ("2026-10-27T15:36:51.155", DROP.format(278)), ("2026-10-27T15:36:51.156", DROP.format(278)))
        events = av.parse_client_log(state, data)
        self.assertEqual(len(events), 1, "Roblox writes the line twice")
        self.assertEqual((events[0]["kind"], events[0]["code"], events[0]["user"]), ("exit", 278, 77))
        self.assertEqual(events[0]["t"], av.calendar.timegm((2026, 10, 27, 15, 36, 51)))
        self.assertFalse(events[0]["memory"])

    def test_critical_memory_counts_only_when_it_was_close(self):
        near = self.log(("2026-10-27T15:36:00.000", LOW), ("2026-10-27T15:36:51.155", DROP.format(278)))
        far = self.log(("2026-10-27T15:00:00.000", LOW), ("2026-10-27T15:36:51.155", DROP.format(278)))
        self.assertTrue(av.parse_client_log({}, near)[0]["memory"])
        self.assertFalse(av.parse_client_log({}, far)[0]["memory"])

    def test_memory_evidence_carries_across_reads(self):
        state = {}
        av.parse_client_log(state, self.log(("2026-10-27T15:36:00.000", LOW)))
        self.assertTrue(av.parse_client_log(state, self.log(("2026-10-27T15:36:51.155", DROP.format(278))))[0]["memory"])

    def test_a_server_move_is_seen_but_a_rejoin_or_new_place_is_not(self):
        state = {"user": 7}
        lines = [("2026-10-27T15:00:00.000", JOIN.format(A)), ("2026-10-27T15:10:00.000", JOIN.format(A)),
                 ("2026-10-27T15:20:00.000", JOIN.format(B))]
        events = av.parse_client_log(state, self.log(*lines))
        self.assertEqual([e["kind"] for e in events], ["moved"])
        state = {"user": 7, "job": A, "place": "999"}
        self.assertEqual(av.parse_client_log(state, self.log(("2026-10-27T15:20:00.000", JOIN.format(B)))), [])

    def test_doctor_reads_only_new_whole_lines_and_finds_the_account_later(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(lambda: av.shutil.rmtree(folder, ignore_errors=True))
        doctor = av.Doctor(os.path.join(folder, "d.json"), folder)
        doctor.since = 0
        path = os.path.join(folder, "0.745_20261031T003520Z_Player_8486C_last.log")
        ignored = os.path.join(folder, "0.745_20261031T003520Z_Player_8486C_CrashHandler_last.log")
        first = self.log(("2026-10-27T15:00:00.100", LOADED.format(77)), ("2026-10-27T15:36:51.155", DROP.format(278)))
        with open(ignored, "wb") as f:
            f.write(first)
        with open(path, "wb") as f:
            f.write(first[:-6])          # the last line is still being written
        self.assertEqual(doctor.scan(), [])
        with open(path, "wb") as f:
            f.write(first)
        self.assertEqual([e["code"] for e in doctor.scan()], [278])
        self.assertEqual(doctor.scan(), [], "nothing is read twice")
        self.assertEqual(doctor.exits[77], av.calendar.timegm((2026, 10, 27, 15, 36, 51)))

    def test_no_log_folder_and_garbage_are_harmless(self):
        self.assertEqual(av.Doctor(os.path.join(tempfile.gettempdir(), "nope.json"), "Z:/missing").scan(), [])
        self.assertEqual(av.parse_client_log({}, b"\x00\xff garbage Sending disconnect with reason: 278\n"), [])

    def test_findings_survive_a_restart_hold_no_text_and_do_not_repeat(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(lambda: av.shutil.rmtree(folder, ignore_errors=True))
        path = os.path.join(folder, "d.json")
        doctor = av.Doctor(path, folder)
        now = time.time()
        self.assertTrue(doctor.add("exit", 77, now, code=278))
        self.assertIsNone(doctor.add("exit", 77, now, code=278))
        self.assertIsNone(doctor.add("crash", 77, now - 8 * 86400), "older than a week isn't kept")
        doctor.add("login", 78, now, lasted="14 min")
        doctor.save()
        again = av.Doctor(path, folder)
        self.assertEqual([i["kind"] for i in again.items], ["exit", "login"])
        self.assertEqual(again.count(("exit",)), 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(av.Doctor(path, folder).items, [])

    def test_every_finding_has_a_full_explanation(self):
        items = [{"kind": "exit", "code": c} for c in (*av.DOCTOR_CODES, 278, 9999)] + [{"kind": k} for k in av.DOCTOR_EVENTS]
        for item in items:
            title, why, fix, action, color = av.explain(item)
            self.assertTrue(title and why and fix, item)
            self.assertIn(color, ("red", "yellow"))
            self.assertTrue(action is None or action in av.DoctorPage.ACTIONS, item)

    def test_the_default_key_cannot_move_a_character(self):
        self.assertEqual(av.AFK_DEFAULT_KEY, "f13")
        self.assertEqual(av.AFK_KEYS[av.AFK_DEFAULT_KEY], 0x7C)

    def test_an_idle_kick_on_f13_says_to_change_the_key(self):
        title, why, fix, action, _ = av.explain({"kind": "exit", "code": 278, "afk_key": "f13"})
        self.assertIn("F13", why)
        self.assertIn("letter", fix)

    def test_an_idle_kick_blames_the_real_cause(self):
        explain = lambda **k: av.explain({"kind": "exit", "code": 278, **k})
        self.assertEqual(explain(anti_afk="off")[3], "afk")
        self.assertIn("off for this account", explain(anti_afk="skipped")[1])
        self.assertIn("critically low", explain(memory=True)[1])
        self.assertNotIn("Anti-AFK was off", explain()[1])


class TestSettingsBackup(unittest.TestCase):
    def test_a_backup_never_holds_logins_or_private_links(self):
        settings = {"multi": True, "geometry": "1x1", "cookie": "SECRET", "private_link": "SECRET"}
        backup = av.settings_backup(settings)
        self.assertEqual(backup, {"altvault_settings": 1, "multi": True})

    def test_restore_takes_only_known_keys_of_the_right_type(self):
        settings = {}
        data = {"altvault_settings": 1, "multi": False, "afk_key": 5, "evil": "x", "favorites": []}
        self.assertEqual(av.restore_backup(settings, data), 2)
        self.assertEqual(settings, {"multi": False, "favorites": []})

    def test_mode_and_main_account_are_backed_up(self):
        backup = av.settings_backup({"mode": "max", "main_account": 42, "cookie": "SECRET"})
        self.assertEqual(backup, {"altvault_settings": 1, "mode": "max", "main_account": 42})
        settings = {}
        av.restore_backup(settings, backup)
        self.assertEqual((av.mode_of(settings), av.main_account(settings)), ("max", 42))

    def test_a_random_file_is_refused(self):
        for bad in ({}, [], "x", {"altvault_settings": 2}):
            with self.assertRaises(ValueError):
                av.restore_backup({}, bad)


class TestAccent(unittest.TestCase):
    def test_each_accent_changes_the_palette_and_main_buttons(self):
        before, buttons = dict(av.C), dict(av.ACCENT)
        self.addCleanup(lambda: (av.C.update(before), av.ACCENT.update(buttons)))
        for name in ("Teal", "Violet", "not a colour"):
            av.apply_accent(name)
            want = av.ACCENTS.get(name, av.ACCENTS["Violet"])["accent"]
            self.assertEqual(av.C["accent"], want)
            self.assertEqual(av.ACCENT["normal"][0], want)


class TestAntiAfkGivesWay(unittest.TestCase):
    def test_user_input_during_the_wait_is_noticed(self):
        ticks = iter([5, 9])
        with mock.patch.object(av, "last_input_tick", lambda: next(ticks)), mock.patch.object(av.time, "sleep"):
            self.assertTrue(av.user_came_back())

    def test_quiet_wait_carries_on(self):
        with mock.patch.object(av, "last_input_tick", lambda: 5), mock.patch.object(av.time, "sleep"):
            self.assertFalse(av.user_came_back())


class TestAntiAfkBlocked(unittest.TestCase):
    """What's in front when Anti-AFK can't switch windows, and the tray alert about it."""

    def describe(self, desktop=1, name="chrome.exe", cls="Chrome_WidgetWin_1", hwnd=42):
        def class_name(h, buf, n):
            buf.value = cls
            return len(cls)
        with mock.patch.object(av._user32, "OpenInputDesktop", lambda *a: desktop), \
                mock.patch.object(av._user32, "CloseDesktop", lambda h: 1), \
                mock.patch.object(av._user32, "GetForegroundWindow", lambda: hwnd), \
                mock.patch.object(av._user32, "GetWindowThreadProcessId", lambda h, p: 1), \
                mock.patch.object(av._user32, "GetClassNameW", class_name), \
                mock.patch.object(av, "_process_name", lambda pid: name):
            return av.describe_foreground()

    def test_blockers_are_named_in_plain_words(self):
        self.assertIn("lock screen", self.describe(desktop=0))
        self.assertIn("lock screen", self.describe(name="lockapp.exe", cls="Windows.UI.Core.CoreWindow"))
        self.assertIn("UAC", self.describe(name="consent.exe", cls="Credential Dialog Xaml Host"))
        self.assertIn("administrator", self.describe(name=""))
        self.assertEqual(self.describe(), "chrome.exe (window class Chrome_WidgetWin_1)")
        self.assertIn("nothing has focus", self.describe(hwnd=0))

    def test_real_foreground_is_described_without_its_title(self):
        text = av.describe_foreground()
        self.assertTrue(text)
        hwnd = av._user32.GetForegroundWindow()
        if hwnd:
            n = av._user32.GetWindowTextLengthW(hwnd)
            buf = av.ctypes.create_unicode_buffer(n + 1)
            av._user32.GetWindowTextW(hwnd, buf, n + 1)
            if len(buf.value) > 3 and buf.value not in ("Program Manager",):
                self.assertNotIn(buf.value, text, "window titles can be private and must not be logged")

    def make_app(self):
        notes = []
        fake = types.SimpleNamespace(afk_misses={}, afk_alerted=False,
                                     tray=types.SimpleNamespace(notify=lambda t, x, warning=False: notes.append((t, x, warning))),
                                     afk_window_name=lambda h: f"@alt{h}")
        fake.afk_alert = types.MethodType(av.App.afk_alert, fake)
        return fake, notes

    def test_one_alert_per_stuck_spell_then_a_recovery_note(self):
        app, notes = self.make_app()
        with mock.patch.object(av.log, "warning"), mock.patch.object(av.log, "info"):
            for miss in range(1, av.AFK_ALERT_AFTER):          # early misses: no alert yet
                app.afk_misses = {1: miss, 2: miss}
                app.afk_alert([1, 2], "the lock screen", 300)
            self.assertEqual(notes, [])
            for miss in range(av.AFK_ALERT_AFTER, av.AFK_ALERT_AFTER + 5):   # stuck: exactly one alert
                app.afk_misses = {1: miss, 2: miss}
                app.afk_alert([1, 2], "the lock screen", 600)
            self.assertEqual(len(notes), 1)
            title, text, warning = notes[0]
            self.assertTrue(warning)
            self.assertIn("@alt1, @alt2", text)
            self.assertIn("the lock screen", text)
            self.assertIn("about 10 min", text)
            app.afk_misses = {}                                  # pings get through again
            app.afk_alert([], None, 600)
            self.assertEqual(notes[-1][0], "Anti-AFK is working again")
            app.afk_alert([], None, 600)
            self.assertEqual(len(notes), 2, "the recovery note is shown once")
            app.afk_misses = {1: av.AFK_ALERT_AFTER}             # a new stuck spell alerts again
            app.afk_alert([1], "consent.exe", 400)
            self.assertEqual(len(notes), 3)

    def test_works_without_a_tray_icon(self):
        app, _ = self.make_app()
        app.tray = None
        app.afk_misses = {1: av.AFK_ALERT_AFTER}
        with mock.patch.object(av.log, "warning") as warned:
            app.afk_alert([1], None, None)
        warned.assert_called_once()


def fake_request(responses, calls):
    """A stand-in for av._request: returns (status, headers, body) from `responses` in order and
    records each call's (url, cookie)."""
    def request(url, cookie=None, method="GET", headers=None, data=None):
        calls.append((url, cookie))
        status, body = responses.pop(0)
        return status, {}, body if isinstance(body, bytes) else av.json.dumps(body).encode()
    return request


SERVER_A = "11111111-1111-1111-1111-111111111111"
SERVER_B = "22222222-2222-2222-2222-222222222222"


class TestServers(unittest.TestCase):
    def test_server_list_is_parsed_and_never_sends_a_cookie(self):
        calls = []
        page = {"nextPageCursor": "NEXT", "data": [
            {"id": SERVER_A, "playing": 3, "maxPlayers": 10, "ping": 40, "fps": 59.9},
            {"id": "not-a-uuid", "playing": 1, "maxPlayers": 10},
            {"id": SERVER_B, "maxPlayers": 8, "ping": None, "fps": None}]}
        with mock.patch.object(av, "_request", fake_request([(200, page)], calls)):
            servers, cursor = av.get_servers("920587237", "CUR", fewest_first=False)
        self.assertEqual([s["id"] for s in servers], [SERVER_A, SERVER_B])
        self.assertEqual(servers[1]["playing"], 0)
        self.assertEqual(cursor, "NEXT")
        url, cookie = calls[0]
        self.assertIsNone(cookie)
        self.assertTrue(av._is_roblox_url(url))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual((query["sortOrder"], query["cursor"], query["excludeFullGames"]), (["Desc"], ["CUR"], ["true"]))

    def test_server_list_errors(self):
        for status, body in ((429, b"{}"), (500, b""), (200, b"nope"), (200, {"data": [{"id": SERVER_A}]})):
            with mock.patch.object(av, "_request", fake_request([(status, body)], [])):
                with self.assertRaises(av.RobloxError):
                    av.get_servers("1")
        with self.assertRaises(ValueError):
            av.get_servers("1/../../evil")     # place IDs must be numbers

    def test_least_busy_server_with_room(self):
        servers = [{"id": "a", "playing": 9, "maxPlayers": 10, "ping": 20},
                   {"id": "b", "playing": 2, "maxPlayers": 10, "ping": 300},
                   {"id": "c", "playing": 2, "maxPlayers": 10, "ping": 50},
                   {"id": "d", "playing": 0, "maxPlayers": 2, "ping": None}]
        self.assertEqual(av.pick_smallest_server(servers, 1)["id"], "d")
        self.assertEqual(av.pick_smallest_server(servers, 4)["id"], "c")   # same players: lower ping wins
        self.assertIsNone(av.pick_smallest_server(servers, 11))


class TestJoinPlayer(unittest.TestCase):
    def test_bad_usernames_never_reach_roblox(self):
        with mock.patch.object(av, "_request", side_effect=AssertionError("request made")):
            for name in ("", "ab", "a" * 21, "bad name", "x/../y", "émoji"):
                self.assertIsNone(av.find_user(name))

    def test_username_lookup(self):
        calls = []
        with mock.patch.object(av, "_request", fake_request([(200, {"data": [{"id": 7, "name": "Builder"}]}),
                                                             (200, {"data": []})], calls)):
            self.assertEqual(av.find_user("builder"), {"id": 7, "name": "Builder"})
            self.assertIsNone(av.find_user("nobody_here"))
        self.assertTrue(all(cookie is None for _, cookie in calls), "a username lookup never sends a cookie")

    def server_of(self, presence, user={"id": 7, "name": "Builder"}):
        with mock.patch.object(av, "find_user", lambda name: user), \
                mock.patch.object(av, "get_presence", lambda cookie, uid: presence):
            return av.server_of_player(FAKE_COOKIE, "@Builder")

    def test_finds_their_server(self):
        self.assertEqual(self.server_of({"userPresenceType": 2, "rootPlaceId": 920587237, "placeId": 1,
                                         "gameId": SERVER_A}), ("920587237", SERVER_A, "Builder"))

    def test_clear_reasons_when_it_cant(self):
        cases = [({"userPresenceType": 1}, "isn't in a game"),
                 ({"userPresenceType": 2, "placeId": 5, "gameId": None}, "privacy"),
                 ({"userPresenceType": 2, "placeId": 5, "gameId": "garbage"}, "privacy")]
        for presence, words in cases:
            with self.assertRaises(av.RobloxError) as caught:
                self.server_of(presence)
            self.assertIn(words, str(caught.exception))
        with self.assertRaises(av.RobloxError) as caught:
            self.server_of({}, user=None)
        self.assertIn("no Roblox user", str(caught.exception))


class TestRamImport(unittest.TestCase):
    ITEMS = [{"SecurityToken": FAKE_COOKIE, "Username": "alt1", "Group": "Farm", "Alias": "main farm",
              "Description": "", "Password": "hunter2-never-read", "UserID": 1},
             {"SecurityToken": FAKE_COOKIE + "B", "Username": "alt2", "Group": "Default", "Alias": "", "Description": "x"},
             {"SecurityToken": "too short", "Username": "broken"}, "not a dict"]

    def write(self, data):
        path = os.path.join(tempfile.mkdtemp(dir=TEMP_HOME), "AccountData.json")
        with open(path, "wb") as f:
            f.write(data)
        return path

    def check(self, found):
        self.assertEqual([f["name"] for f in found], ["alt1", "alt2"])
        self.assertEqual(found[0], {"cookie": FAKE_COOKIE, "name": "alt1", "group": "Farm", "note": "main farm"})
        self.assertIsNone(found[1]["group"], "RAM's Default group means no group")
        self.assertNotIn("hunter2", repr(found), "saved passwords must never be read")

    def test_plain_file(self):
        self.check(av.read_ram_accounts(self.write(av.json.dumps(self.ITEMS).encode())))

    def test_encrypted_file(self):
        blob = av._dpapi(av._crypt32.CryptProtectData, av.json.dumps(self.ITEMS).encode(), av.RAM_ENTROPY)
        self.check(av.read_ram_accounts(self.write(blob)))

    def test_password_file_and_garbage_are_explained(self):
        for data, words in ((av.RAM_PASSWORD_HEADER + b"\x00" * 40, "password"),
                            (b"\x01\x02garbage", "decrypt"), (b'{"not": "a list"}', "isn't a RAM")):
            with self.assertRaises(av.RobloxError) as caught:
                av.read_ram_accounts(self.write(data))
            self.assertIn(words, str(caught.exception))

    def test_import_checks_logins_then_saves_once(self):
        path = self.write(av.json.dumps(self.ITEMS).encode())
        saved, reports = [], []

        def fake_user(cookie):
            if cookie.endswith("B"):
                raise av.CookieExpired()
            return {"id": 1, "name": "alt1", "displayName": "Alt One"}
        app = types.SimpleNamespace(ui=lambda fn, *a: fn(*a), set_status=lambda t: None,
                                    add_many=saved.append)
        with mock.patch.object(av, "get_user", fake_user):
            av.import_ram_into(app, path, reports.append)
        self.assertEqual(len(saved), 1)
        self.assertEqual([u["name"] for u, _ in saved[0]], ["alt1"])
        self.assertIn("Imported 1 account.", reports[0])
        self.assertIn("alt2", reports[0])


class TestWindow(unittest.TestCase):
    """The real AltVault window, kept hidden, with test accounts in the temporary data folder."""

    @classmethod
    def setUpClass(cls):
        import tkinter
        try:
            cls.root = tkinter.Tk()
        except tkinter.TclError as e:
            raise unittest.SkipTest(f"no display: {e}")
        cls.root.withdraw()
        accounts = [{"id": i, "name": f"alt{i}", "displayName": f"Alt {i}", "cookie": FAKE_COOKIE, "note": ""}
                    for i in range(1, 6)]
        accounts[0]["group"] = accounts[1]["group"] = "Farm"
        accounts[2]["group"] = "Mains"
        with mock.patch.object(av.threading, "Thread", lambda **k: types.SimpleNamespace(start=lambda: None)), \
                mock.patch.object(av, "UI_DIR", os.path.join(TEMP_HOME, "no-ui")):   # the classic window only
            cls.app = av.App(cls.root, accounts)
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_groups_in_the_list(self):
        tree = self.app.tree
        self.assertEqual(list(tree.get_children()), ["group:Farm", "group:Mains", "4", "5"])
        self.assertEqual(list(tree.get_children("group:Farm")), ["1", "2"])
        tree.selection_set("group:Farm")
        self.assertEqual([a["name"] for a in self.app.selected_accounts()], ["alt1", "alt2"])
        self.assertEqual(sorted(self.app.account_rows()), ["1", "2", "3", "4", "5"])

    def test_mode_switches_stay_in_step_and_show_on_accounts(self):
        app = self.app
        self.assertEqual(len(app.mode_controls), 2, "one on Home, one in Settings")
        self.addCleanup(lambda: [app.settings.pop(k, None) for k in ("mode", "main_account")])
        with mock.patch.object(av, "save_settings", lambda s: None), mock.patch.object(app, "toast"):
            app.mode_controls[0].set("grind", notify=True)          # clicking Grind on Home
            self.assertEqual(app.settings["mode"], "grind")
            self.assertEqual([c.value for c in app.mode_controls], ["grind", "grind"])
            app.set_main(app.account(3))
            self.assertIn("main", app.status_of(app.account(3))[0])
            self.assertNotIn("15 FPS", app.status_of(app.account(3))[0])
            self.assertIn("15 FPS · low graphics · muted", app.status_of(app.account(4))[0])
            settings = app.pages["settings"]
            settings.show_tab("modes")
            self.assertEqual(settings.main_var.get(), "@alt3")
            settings.main_box.current(0)                            # "None"
            settings.set_main()
            settings.show_tab("general")
            self.assertIsNone(av.main_account(app.settings))
            app.set_mode("normal")
            self.assertEqual([c.value for c in app.mode_controls], ["normal", "normal"])
        self.assertNotIn("FPS", app.status_of(app.account(4))[0])

    def test_new_settings_save_and_explain_themselves(self):
        app, page = self.app, self.app.pages["settings"]
        self.addCleanup(lambda: [app.settings.pop(k, None) for k in ("launch_delay",)])
        with mock.patch.object(av, "save_settings", lambda s: None):
            page.delay_var.set("20 seconds")
            page.set_delay()
            self.assertEqual(app.settings["launch_delay"], 20)
            page.hook_var.set("https://example.com/not-a-webhook")
            page.save_hook()
        self.assertIn("isn't a Discord webhook", page.hook_state.cget("text"))
        self.assertIsNone(app.webhook)
        page.show_tab("alerts")
        page.show_tab("general")

    def test_window_buttons_wait_for_roblox(self):
        self.app.update_window_buttons(0)
        self.assertEqual(str(self.app.min_btn.cget("state")), "disabled")
        with mock.patch.object(av._user32, "IsIconic", lambda h: True):
            self.app.update_window_buttons(1, [(1, 2, "Roblox")])
        self.assertEqual(self.app.min_btn.cget("text"), "Restore all")
        self.app.update_window_buttons(0)

    def test_home_points_to_a_new_release(self):
        home = self.app.pages["home"]
        self.app.update_available = ("9.9.9", av.GITHUB_URL + "/releases/tag/v9.9.9")
        self.addCleanup(setattr, self.app, "update_available", None)
        item = [i for i in home.attention() if i[2] == "update"]
        self.assertEqual(len(item), 1)
        self.assertIn("9.9.9", item[0][1])
        with mock.patch.object(av.webbrowser, "open") as opened:
            home.open_row(types.SimpleNamespace(target="update"))
        opened.assert_called_once_with(av.GITHUB_URL + "/releases/tag/v9.9.9")

    def test_diagnostics_hold_no_names_or_logins(self):
        av.log.warning("Launch of alt3 failed: Roblox is down (%s)", os.path.expanduser("~"))
        text = self.app.diagnostics()
        self.assertIn("Launch of account 3 failed", text)
        self.assertNotIn("alt3", text)
        self.assertNotIn(os.path.expanduser("~"), text)
        self.assertNotIn(FAKE_COOKIE[:20], text)
        self.assertIn(f"AltVault {av.APP_VERSION}", text)

    def test_every_new_page_gets_clean_data(self):
        for page in ("home", "profiles", "games", "live", "stats", "pulse", "doctor", "settings"):
            data = self.app.web_page_data(page)
            text = av.json.dumps(data)   # must be plain JSON for the page
            self.assertNotIn(FAKE_COOKIE[:30], text, page)
        self.assertIsNone(self.app.web_page_data("nonsense"))

    def test_the_page_cant_set_bad_values_or_touch_other_windows(self):
        app = self.app
        before = dict(app.settings)
        with mock.patch.object(av, "save_settings", lambda s: None):
            for key, value in (("launch_delay", "abc"), ("launch_delay", 7), ("afk_key", "<script>"), ("afk_key", "ab"),
                               ("mode", "turbo"), ("multi", "yes"), ("cookie", "x"), ("clean_logs", "false")):
                app.web_set(key, value)
        self.assertEqual(app.settings, before)
        with mock.patch.object(av, "roblox_windows", lambda: [(5555, 77, "Roblox")]), \
                mock.patch.object(av, "focus_window") as focus:
            app.web_do("show-window", {"hwnd": 1234})   # not a Roblox window
            focus.assert_not_called()
            app.web_do("show-window", {"hwnd": 5555})
            focus.assert_called_once_with(5555)
        app.web_do("no-such-button", {})

    def test_double_clicking_a_group_never_joins(self):
        event = types.SimpleNamespace(y=5)
        with mock.patch.object(self.app, "join_selected") as join:
            with mock.patch.object(self.app.tree, "identify_row", lambda y: "group:Farm"):
                self.app.on_double_click(event)
            join.assert_not_called()
            with mock.patch.object(self.app.tree, "identify_row", lambda y: "4"):
                self.app.on_double_click(event)
            join.assert_called_once()

    def test_moving_between_groups(self):
        with mock.patch.object(av, "save_accounts", lambda a: None):
            self.app.set_group([self.app.account(4)], "Farm")
            self.assertEqual(list(self.app.tree.get_children("group:Farm")), ["1", "2", "4"])
            self.app.set_group([self.app.account(4)], None)
        self.assertIn("4", self.app.tree.get_children())

    def test_favourites_star(self):
        with mock.patch.object(av, "save_settings", lambda s: None):
            self.app.place_var.set("Tap Simulator  (72858262610964)")
            self.app.toggle_favorite()
            self.assertEqual(self.app.star_btn.cget("text"), "★")
            self.assertEqual(self.app.place_box["values"][0], "★ Tap Simulator  (72858262610964)")
            self.app.place_var.set("★ Tap Simulator  (72858262610964)")    # picking it from the list
            self.assertEqual(av.parse_place_id(self.app.place_var.get()), "72858262610964")
            self.app.toggle_favorite()
            self.assertEqual(self.app.star_btn.cget("text"), "☆")

    def test_server_browser_joins_the_chosen_server(self):
        servers = [{"id": SERVER_A, "playing": 5, "maxPlayers": 10, "ping": 30, "fps": 60.0},
                   {"id": SERVER_B, "playing": 1, "maxPlayers": 10, "ping": 80, "fps": 59.5}]
        self.app.tree.selection_set(["4", "5"])
        with mock.patch.object(av, "get_servers", lambda *a: (servers, "")), \
                mock.patch.object(av.threading, "Thread", lambda target, daemon=None: types.SimpleNamespace(start=target)), \
                mock.patch.object(self.app, "join_selected") as join:
            self.app.ui = lambda fn, *a: fn(*a)
            browser = av.ServerBrowser(self.app, "72858262610964")
            self.assertEqual(browser.heading.cget("text"), "2 servers")
            browser.join_smallest()
            del self.app.ui
        self.assertEqual(self.app.job_var.get(), SERVER_B)
        self.assertEqual(self.app.place_var.get(), "72858262610964")
        join.assert_called_once()
        self.app.job_var.set("")

    def test_status_shows_per_account_settings(self):
        self.app.settings.update(fps_caps={"5": 15}, low_graphics=[5], muted=[5])
        text, _ = self.app.status_of(self.app.account(5))
        self.assertIn("15 FPS · low graphics · muted", text)
        for key in ("fps_caps", "low_graphics", "muted"):
            self.app.settings.pop(key)

    def right_click(self, row):
        """The labels of the menu a right-click on `row` opens, with its submenus' labels."""
        shown = []

        def labels(menu):
            items = []
            for i in range(menu.index("end") + 1):
                if menu.type(i) == "separator":
                    continue
                label = menu.entrycget(i, "label")
                if menu.type(i) == "cascade":
                    sub = self.root.nametowidget(menu.entrycget(i, "menu"))
                    items.append((label, labels(sub)))
                else:
                    items.append(label)
            return items
        self.app.tree.selection_set(row)
        with mock.patch.object(self.app.tree, "identify_row", lambda y: row), \
                mock.patch.object(av.tk.Menu, "tk_popup", lambda menu, x, y: shown.append(labels(menu))):
            self.app.show_menu(types.SimpleNamespace(y=1, x_root=0, y_root=0))
        return shown[0]

    def test_account_menu_is_grouped(self):
        top = self.right_click("4")
        names = [item if isinstance(item, str) else item[0] for item in top]
        self.assertEqual(names, ["Join game", "Join a player's server...", "Performance", "Group", "Account", "Remove..."])
        performance = dict(i for i in top if isinstance(i, tuple))["Performance"]
        self.assertEqual([i if isinstance(i, str) else i[0] for i in performance],
                         ["Main account (keeps full quality in every mode)", "FPS cap", "Lowest graphics", "Mute",
                          "✓ Anti-AFK"])
        account = dict(i for i in top if isinstance(i, tuple))["Account"]
        self.assertIn("Log in again...", account)

    def test_group_header_menu(self):
        names = [i if isinstance(i, str) else i[0] for i in self.right_click("group:Farm")]
        self.assertEqual(names, ["Join game (2 accounts)", "Join a player's server...", "Performance",
                                 "Rename group...", "Ungroup (keeps the accounts)"])

    def test_join_button_counts_the_selection(self):
        self.app.tree.selection_set(["4", "5"])
        self.app.update_join_label()
        self.assertEqual(self.app.join_btn.cget("text"), "Join 2 accounts")
        self.app.tree.selection_set("group:Farm")
        self.app.update_join_label()
        self.assertEqual(self.app.join_btn.cget("text"), "Join 2 accounts")
        self.app.tree.selection_set(())
        self.app.update_join_label()
        self.assertEqual(self.app.join_btn.cget("text"), "Join")

    def test_anti_afk_off_for_several_accounts(self):
        with mock.patch.object(av, "save_settings", lambda s: None):
            self.app.set_afk_skip([self.app.account(4), self.app.account(5)], True)
            self.assertEqual(self.app.afk_skip_ids(), {4, 5})
            self.app.set_afk_skip([self.app.account(4)], False)
            self.assertEqual(self.app.afk_skip_ids(), {5})
            self.app.set_afk_skip([self.app.account(5)], False)

    def test_settings_window(self):
        saved = []
        with mock.patch.object(av, "save_settings", lambda s: saved.append(dict(s))):
            page = self.app.pages["settings"]
            page.key_var.set("f13")
            page.set_key()
        self.assertEqual(saved[-1]["afk_key"], "f13")
        self.app.settings.pop("afk_key")

    def test_sidebar_switches_pages(self):
        for key in ("live", "settings", "accounts"):
            self.app.show_page(key)
            self.assertEqual(self.app.page, key)
            shown = [k for k, page in self.app.pages.items() if page.winfo_manager()]
            self.assertEqual(shown, [key])

    def test_stream_mode_hides_names(self):
        with mock.patch.object(av, "save_settings", lambda s: None), mock.patch.object(self.app, "tick_now"):
            self.app.stream_var.set(True)
            self.app.toggle_stream()
            values = self.app.tree.item("4", "values")
            self.assertEqual(values[0], "Account 4")
            self.assertNotIn("user", self.app.tree["displaycolumns"])
            self.assertEqual(self.app.window_title(self.app.account(4)), "Roblox - Account 4")
            self.assertNotIn("alt", self.app.status.cget("text"))
            self.app.stream_var.set(False)
            self.app.toggle_stream()
        self.assertEqual(self.app.tree.item("4", "values")[1], "@alt4")
        self.assertEqual(self.app.window_title(self.app.account(4)), "Roblox - alt4")

    def test_live_tiles_follow_the_windows(self):
        page = self.app.pages["live"]
        registered, released = [], []
        windows = [(101, 1, "Roblox"), (102, 2, "Roblox - alt2")]
        self.app.running[2] = 2
        self.app.presence[2] = {"userPresenceType": 2}
        with mock.patch.object(av, "roblox_windows", lambda: list(windows)), \
                mock.patch.object(av, "responsive", lambda h: h != 101), \
                mock.patch.object(av, "register_thumbnail", lambda d, s: registered.append(s) or s), \
                mock.patch.object(av, "unregister_thumbnail", released.append), \
                mock.patch.object(av, "place_thumbnail", lambda t, r: True), \
                mock.patch.object(av, "client_size", lambda h: (800, 600)), \
                mock.patch.object(av._user32, "IsIconic", lambda h: False):
            self.app.show_page("live")
            self.assertEqual(set(page.tiles), {101, 102})
            self.assertEqual(page.tiles[101].color, "red")
            self.assertEqual(page.tiles[102].color, "green")
            self.assertEqual(page.tiles[102].label.cget("text"), "@alt2")
            page.thumbs.update({101: 101, 102: 102})   # as place_thumbs would once the tiles are drawn
            windows.pop(0)
            page.sync(av.roblox_windows())
            self.assertEqual(set(page.tiles), {102})
            self.assertEqual(released, [101])
            self.app.show_page("accounts")
            self.assertEqual(released, [101, 102], "thumbnails must go when the page is left")
            self.assertIsNone(page.sample_job)
        self.app.running.clear()
        self.app.presence.clear()

    def test_import_carries_settings_without_undoing_local_ones(self):
        self.app.settings["fps_caps"] = {"4": 30}
        entries = av.export_entries({"accounts": [
            {"cookie": FAKE_COOKIE, "fps_cap": 15, "muted": True, "group": "Imported"},
            {"cookie": FAKE_COOKIE, "fps_cap": 20, "afk_skip": True}]})
        users = [{"id": 4, "name": "alt4", "displayName": "Alt 4"}, {"id": 99, "name": "new", "displayName": "New"}]
        with mock.patch.object(av, "save_accounts", lambda a: None), mock.patch.object(av, "save_settings", lambda s: None), \
                mock.patch.object(av.threading, "Thread", lambda **k: types.SimpleNamespace(start=lambda: None)), \
                mock.patch.object(self.app, "refresh_presence"):
            self.app.add_many(list(zip(users, entries)))
        self.assertEqual(self.app.fps_caps(), {"4": 30, "99": 20})
        self.assertIn(4, self.app.flagged("muted"))
        self.assertIn(99, self.app.afk_skip_ids())
        self.assertEqual(self.app.group_of(self.app.account(4)), "Imported")
        self.app.accounts.remove(self.app.account(99))
        self.app.account(4).pop("group")
        for key in ("fps_caps", "muted", "low_graphics", "afk_skip"):
            self.app.settings.pop(key, None)
        self.app.refresh()

    def pump(self, ms):
        """Runs the window's events for about `ms` milliseconds."""
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.005)

    def wait_until(self, condition, timeout=5.0):
        """Runs events until `condition()` is true. Animations take as long as they take on a slow
        machine, so tests wait for the result instead of guessing how many milliseconds it needs."""
        end = time.monotonic() + timeout
        while not condition():
            self.assertLess(time.monotonic(), end, "gave up waiting")
            self.root.update()
            time.sleep(0.005)

    def test_sidebar_pill_glides_and_comes_to_rest(self):
        rail = self.app.rail
        self.root.deiconify()
        self.wait_until(rail.winfo_ismapped)       # the glide only runs once the window is really on screen
        clock = [1000.0]
        try:
            self.app.show_page("accounts"); self.wait_until(lambda: not rail.jobs)
            rail.update_idletasks()
            top, target = rail.pos["accounts"], rail.pos["stats"]
            self.assertEqual(rail.sel_y, top)
            with mock.patch.object(av.time, "perf_counter", lambda: clock[0]):   # a clock the test controls
                self.app.show_page("stats")        # the glide starts at clock 1000.0
                clock[0] += 0.115                  # half way through its 230 ms
                self.pump(40)
                self.assertTrue(top < rail.sel_y < target, "mid-glide the pill is between the two items")
                clock[0] += 5                      # long past the end
                self.wait_until(lambda: not rail.jobs)
            self.assertEqual(rail.sel_y, target)
            self.assertEqual(rail.jobs, {}, "no animation timer may be left running at rest")
            self.assertEqual(rail.shades[rail.icon["stats"]], av.C["accent_text"], "the lit icon takes the accent colour")
        finally:
            self.root.withdraw()
            self.app.show_page("accounts"); self.pump(300)

    def test_sidebar_hover_pill_follows_the_mouse_and_leaves(self):
        rail = self.app.rail
        self.root.deiconify()
        self.wait_until(rail.winfo_ismapped)
        try:
            self.pump(100); rail.update_idletasks()
            y = lambda k: rail.pos[k] + rail.h // 2
            rail.motion(types.SimpleNamespace(x=20, y=y("games")))
            self.assertEqual(rail.hover_y, rail.pos["games"])
            rail.motion(types.SimpleNamespace(x=20, y=y("pulse")))
            self.wait_until(lambda: rail.hover_y == rail.pos["pulse"])
            self.assertEqual(rail.hover_y, rail.pos["pulse"])
            self.assertEqual(rail.click(types.SimpleNamespace(x=20, y=y("pulse"))), None)
            self.assertEqual(self.app.page, "pulse")
            rail.leave()
            self.assertIsNone(rail.hover_y)
            self.assertEqual(rail.itemcget(rail.hover_id, "state"), "hidden")
        finally:
            self.root.withdraw()
            self.app.show_page("accounts"); self.pump(300)

    def test_read_only_and_disabled_boxes_stay_dark(self):
        """Windows' default read-only look is light grey, which showed at the rounded corners."""
        from tkinter import ttk
        style = ttk.Style(self.root)
        for state in ("readonly", "disabled"):
            self.assertEqual(style.lookup("TEntry", "background", [state]), av.C["bg"], state)
            self.assertEqual(style.lookup("TEntry", "fieldbackground", [state]), av.C["field"], state)

    def test_live_gauges_never_blink(self):
        """CPU and memory change on every reading; blinking them cost real power (Pulse used 3% of a core)."""
        pulse = self.app.pages["pulse"]
        self.assertTrue(all(card.blink is False for card in pulse.cards.values()))
        card = pulse.cards["pc_cpu"]
        card.set("10%")
        card.set("20%")
        self.assertEqual(card.value.__dict__.get("_fade_jobs", {}), {}, "no fade timer may be running for a gauge")
        stats = self.app.pages["stats"]
        self.assertTrue(all(card.blink for card in stats.cards.values()), "event counts do still blink")

    def test_changed_values_flash_then_settle(self):
        import tkinter
        label = tkinter.Label(self.root, text="3", fg="#ebe8f0")
        av.set_value(label, "4")
        self.assertNotEqual(label.cget("fg"), "#ebe8f0", "a changed value blinks")
        settled = lambda: not label.__dict__.get("_fade_jobs") and label.cget("fg") == "#ebe8f0"
        self.wait_until(settled)
        av.set_value(label, "—")
        self.wait_until(settled)
        av.set_value(label, "5")
        self.assertEqual(label.cget("fg"), "#ebe8f0", "the first real value doesn't blink")
        label.destroy()

    def test_fade_reaches_its_target_and_can_be_interrupted(self):
        import tkinter
        label = tkinter.Label(self.root, bg="#000000")
        av.fade(label, "bg", "#ffffff")
        av.fade(label, "bg", "#ff0000")          # a new fade takes over the running one
        self.wait_until(lambda: not label.__dict__["_fade_jobs"])
        self.assertEqual(label.cget("bg"), "#ff0000")
        self.assertEqual(label.__dict__["_fade_jobs"], {}, "no timer may be left running at rest")
        av.fade(label, "bg", "#00ff00")
        label.destroy()                           # destroyed mid-fade: must not raise
        self.root.update()

    def test_join_spinner_runs_only_while_joining(self):
        self.app.joining = True
        self.app.spin(3)
        self.assertIn("Joining", self.app.join_btn.cget("text"))
        self.app.joining = False
        before = self.app.join_btn.cget("text")
        self.app.spin(4)
        self.assertEqual(self.app.join_btn.cget("text"), before)
        self.app.update_join_label()

    def test_stress_pages_windows_and_stream_mode(self):
        """Many rapid page switches, windows coming and going and stream-mode flips: no errors, and
        every thumbnail is released when Live is left."""
        live = self.app.pages["live"]
        registered, released = set(), []
        windows = [(200 + i, 300 + i, "Roblox") for i in range(12)]
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(av, "ask_yes_no", lambda *a, **k: False).start()   # a stray real click must not block the run
        with mock.patch.object(av, "roblox_windows", lambda: list(windows)),                 mock.patch.object(av, "register_thumbnail", lambda d, h: registered.add(h) or h),                 mock.patch.object(av, "unregister_thumbnail", released.append),                 mock.patch.object(av, "place_thumbnail", lambda t, r: True),                 mock.patch.object(av, "client_size", lambda h: (800, 600)),                 mock.patch.object(av, "save_settings", lambda s: None),                 mock.patch.object(av._user32, "IsIconic", lambda h: False):
            for i in range(30):
                self.app.show_page(self.app.NAV[i % len(self.app.NAV)][0])
                if i % 3 == 0:
                    if len(windows) > 4:
                        del windows[:2]
                    else:
                        windows.extend([(500 + i, 600 + i, "Roblox"), (700 + i, 800 + i, "Roblox")])
                if i % 5 == 0:
                    self.app.stream_var.set(not self.app.stream_var.get())
                    self.app.refresh()
                self.root.update()
            self.app.stream_var.set(False)
            self.app.show_page("live")
            self.root.update()
            self.app.show_page("accounts")
        self.assertEqual(live.thumbs, {}, "thumbnails must all be released")
        self.assertIsNone(live.sample_job)

    def test_group_chevron_follows_open_and_closed(self):
        tree, row = self.app.tree, "group:Mains"
        chevron = lambda: str(tree.item(row, "image")[0])
        self.assertEqual(chevron(), str(self.app.chevron(True)))
        click = types.SimpleNamespace(x=1, y=1)
        with mock.patch.object(av, "save_settings", lambda s: None), \
                mock.patch.object(tree, "identify_row", lambda y: row), \
                mock.patch.object(tree, "identify_element", lambda x, y: "Treeitem.image"):
            self.assertEqual(self.app.tree_click(click), "break")
            self.assertFalse(tree.item(row, "open"))
            self.assertEqual(chevron(), str(self.app.chevron(False)))
            self.app.tree_click(click)
        self.assertTrue(tree.item(row, "open"))
        self.app.settings.pop("collapsed_groups", None)

    def test_names_tk_cannot_draw_are_cleaned(self):
        self.assertEqual(av.clean_name("[\U0001F383] Tap Simulator REBORN"), "Tap Simulator REBORN")
        self.assertEqual(av.clean_name("[\U0001F383 UPD] Bee Swarm"), "[UPD] Bee Swarm")
        with mock.patch.dict(self.app.settings, {"recents": [{"id": "1", "name": "\U0001F95A Eggs \U0001F525"}]}):
            self.assertEqual(self.app.recents()[0]["name"], "Eggs")

    def test_every_sidebar_page_opens(self):
        self.assertEqual([k for k, _, _ in self.app.NAV],
                         ["home", "accounts", "profiles", "games", "live", "stats", "pulse", "doctor", "settings"])
        with mock.patch.object(av, "roblox_windows", lambda: []):
            for key, _, _ in self.app.NAV:
                self.app.show_page(key)
                self.assertEqual(self.app.page, key)
            self.app.show_page("accounts")

    def test_profile_launch_selects_its_accounts(self):
        with mock.patch.object(self.app, "join_selected") as join:
            self.app.search_var.set("alt4")
            self.app.launch_profile({"name": "Farm", "accounts": [1, 2, 404], "place": "123", "place_name": "Egg Game"})
        join.assert_called_once()
        self.assertEqual(self.app.search_var.get(), "")
        self.assertEqual([a["id"] for a in self.app.selected_accounts()], [1, 2])
        self.assertEqual(av.parse_place_id(self.app.place_var.get()), "123")
        self.app.tree.selection_set(())
        self.app.place_var.set("")

    def test_games_page_picks_a_game_for_joining(self):
        page = self.app.pages["games"]
        with mock.patch.object(av, "save_settings", lambda s: None), \
                mock.patch.object(page, "load_icons", lambda places: None):
            self.app.settings["recents"] = [{"id": "55", "name": "Egg Game"}]
            page.refresh()
            page.tree.selection_set("55")
            page.toggle_star()
            self.assertIn("55", {f["id"] for f in self.app.favorites()})
            page.pick()
        self.assertEqual(self.app.page, "accounts")
        self.assertEqual(av.parse_place_id(self.app.place_var.get()), "55")
        for key in ("recents", "favorites"):
            self.app.settings.pop(key)
        self.app.place_var.set("")
        self.app.update_recents_box()

    def test_stats_page_reads_the_history(self):
        saved, self.app.history = self.app.history, av.History(os.path.join(av.APP_DIR, "history-window.json"))
        try:
            self.app.history.add(launch_ok=3, launch_fail=1, pings=40, missed=2, closed=1)
            self.app.history.sample(open=4, ingame=3)
            page = self.app.pages["stats"]
            page.refresh()
            page.on_hide()
            self.assertEqual(page.cards["launches"].value.cget("text"), "75.0%")
            self.assertEqual(page.cards["ingame"].value.cget("text"), "75.0%")
            self.assertEqual(page.cards["pings"].value.cget("text"), "40")
            self.assertEqual(page.cards["closed"].value.cget("text"), "1")
        finally:
            self.app.history = saved

    def test_private_link_is_kept_when_pasted_and_can_be_forgotten(self):
        link = 'https://www.roblox.com/share?code=SECRET123&type=Server'
        self.addCleanup(self.app.place_var.set, '')
        self.app.place_var.set(link)
        self.assertEqual(av.load_private_link(), link)
        self.assertEqual(self.app.place_box['values'][0], link)
        self.app.place_var.set('')
        self.assertEqual(av.load_private_link(), link, 'clearing the box must not lose it')
        self.app.forget_private_link()
        self.assertIsNone(av.load_private_link())
        self.assertNotIn(link, self.app.place_box['values'])

    def test_card_rims_are_one_solid_colour_where_they_stretch(self):
        """Tk repeats everything outside an image's border, so a changing rim there shows as dashes."""
        images, top = self.app.root.altvault_images, self.app.root.altvault_glass_top
        for name in ("AVGlass", "AVGlassHover"):
            picture = images[name]
            for x in (0, 1, picture.width() - 1, picture.width() - 2):
                column = {picture.get(x, y) for y in range(top, picture.height() - top // 2)}
                self.assertEqual(len(column), 1, f"{name} rim changes colour at x={x}")

    def doctor_app(self):
        saved = self.app.doctor
        self.addCleanup(setattr, self.app, "doctor", saved)
        self.addCleanup(self.app.doctor_closed.clear)
        self.addCleanup(self.app.pages["doctor"].refresh)
        folder = tempfile.mkdtemp()
        self.addCleanup(lambda: av.shutil.rmtree(folder, ignore_errors=True))
        self.app.doctor = av.Doctor(os.path.join(folder, "d.json"), folder)
        return self.app.doctor

    def event(self, code, user=4, kind="exit", memory=False, age=10):
        return {"t": time.time() - age, "user": user, "kind": kind, "code": code, "memory": memory}

    def test_a_normal_leave_is_not_reported_but_a_drop_is(self):
        doctor = self.doctor_app()
        with mock.patch.object(self.app, "alert") as alert:
            self.app.doctor_absorb([self.event(285)], False)
            self.assertEqual(doctor.items, [])
            alert.assert_not_called()
            self.app.doctor_absorb([self.event(278)], False)
            self.assertEqual(doctor.items[0]["code"], 278)
            alert.assert_called_once()

    def test_the_first_scan_files_old_events_without_alerting(self):
        doctor = self.doctor_app()
        with mock.patch.object(self.app, "alert") as alert:
            self.app.doctor_absorb([self.event(278, age=5000)], True)
        alert.assert_not_called()
        self.assertEqual(len(doctor.items), 1)
        self.assertNotIn("anti_afk", doctor.items[0], "today's switch says nothing about an old event")

    def test_a_window_that_vanishes_with_no_reason_is_a_crash_but_a_goodbye_is_not(self):
        doctor = self.doctor_app()
        now = time.time()
        with mock.patch.object(self.app, "alert"):
            self.app.doctor_closed.append((4, now))
            self.app.doctor_absorb([], False)
            self.assertEqual([i["kind"] for i in doctor.items], ["crash"])
            doctor.items.clear()
            doctor.exits[5] = now - 2
            self.app.doctor_closed.append((5, now))
            self.app.doctor_absorb([], False)
            self.assertEqual(doctor.items, [])

    def test_doctor_page_shows_why_and_offers_the_fix(self):
        doctor = self.doctor_app()
        doctor.add("login", 4, lasted="14 min")
        doctor.add("exit", 123456, time.time() - 60, code=9999)
        self.app.show_page("doctor")
        self.pump(50)
        page = self.app.pages["doctor"]
        self.assertEqual(len(page.tree.get_children()), 2)
        self.assertEqual(page.title.cget("text"), "Login rejected")
        self.assertIn("14 min", page.why.cget("text"))
        self.assertEqual(page.action.cget("text"), "Log in again")
        page.tree.selection_set(page.tree.get_children()[1])
        self.pump(50)
        self.assertIn("error 9999", page.title.cget("text"))
        self.assertEqual(page.who(page.current), "Not in AltVault")
        self.app.show_page("home")

    def test_a_log_on_disk_becomes_a_finding_on_doctor_and_home(self):
        doctor = self.doctor_app()
        doctor.since = time.time() - 3600
        when = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 30))
        lines = (roblox_line(when + ".100", LOADED.format(self.app.accounts[0]["id"])) + roblox_line(when + ".200", LOW)
                 + roblox_line(when + ".300", DROP.format(278)))
        with open(os.path.join(doctor.folder, "0.745_20261031T003520Z_Player_8486C_last.log"), "wb") as f:
            f.write(lines)
        with mock.patch.object(av.threading, "Thread", lambda target, daemon: types.SimpleNamespace(start=target)),                 mock.patch.object(self.app, "alert") as alert:
            self.app.doctor_scan()
            self.pump(50)
        self.assertFalse(self.app.doctor_busy, "the scan must always finish")
        alert.assert_called_once()
        self.assertEqual((doctor.items[0]["code"], doctor.items[0]["memory"]), (278, True))
        self.assertTrue(os.path.exists(doctor.path))
        with open(doctor.path, encoding="utf-8") as f:
            self.assertNotIn("Sending", f.read())
        self.assertTrue(any("Idle-kicked" in text for _, text, _ in self.app.pages["home"].attention()))

    def test_doctor_page_when_nothing_went_wrong(self):
        self.doctor_app()
        self.app.pages["doctor"].refresh()
        self.assertEqual(self.app.pages["doctor"].title.cget("text"), "All clear")

    def test_stream_mode_hides_names_on_doctor(self):
        doctor = self.doctor_app()
        doctor.add("login", self.app.accounts[0]["id"])
        self.app.stream_var.set(True)
        self.addCleanup(self.app.stream_var.set, False)
        page = self.app.pages["doctor"]
        self.assertTrue(page.who(doctor.items[0]).startswith("Account "))

    def test_home_page_reports_and_never_crashes_on_an_empty_state(self):
        self.app.show_page("home")
        self.pump(50)
        home = self.app.pages["home"]
        home.refresh()
        self.assertTrue(home.job, "it refreshes while on screen")
        self.app.show_page("accounts")
        self.assertIsNone(home.job, "and stops as soon as it isn't")

    def test_home_attention_lists_what_matters_most_first(self):
        home = self.app.pages["home"]
        self.app.expired.add(99)
        self.addCleanup(self.app.expired.discard, 99)
        self.assertIn("expired", home.attention()[0][1])

    def test_sidebar_collapses_and_remembers(self):
        rail = self.app.rail
        start = rail.wide
        self.addCleanup(self.app.set_sidebar, start)
        with mock.patch.object(av, "save_settings"):
            self.app.set_sidebar(False)
            self.pump(50)
            self.assertEqual(rail.itemcget(rail.label["live"], "state"), "hidden")
            self.assertFalse(self.app.settings["sidebar_wide"])
            self.app.set_sidebar(True)
            self.pump(50)
            self.assertEqual(rail.itemcget(rail.label["live"], "state"), "normal")

    def test_a_toast_replaces_the_last_and_goes_away(self):
        self.app.toast("One")
        first = self.app.toast_box
        self.app.toast("Two", "text", warning=True)
        self.assertFalse(first.winfo_exists())
        self.app.hide_toast(self.app.toast_box)
        self.assertIsNone(self.app.toast_box)

    def test_windows_not_from_altvault_do_not_skew_time_in_game(self):
        saved, self.app.history = self.app.history, av.History(os.path.join(av.APP_DIR, "history-linked.json"))
        windows = [(1, 4001, "Roblox - alt4"), (2, 4002, "Roblox")]   # the second was opened by hand
        self.app.running[4001] = 4
        self.app.presence[4] = {"userPresenceType": 2}
        try:
            with mock.patch.object(av, "measure", lambda w, a, b: {"pc_cpu": 10.0, "pc_ram": 50.0, "rbx_cpu": 5.0,
                                                                   "rbx_ram": 20.0, "windows": len(w)}):
                self.app.sample_minute(windows)
            bucket = self.app.history.hours[self.app.history.hour()]
            self.assertEqual((bucket["open"], bucket["ingame"]), (1, 1))
        finally:
            self.app.history = saved
            self.app.running.pop(4001, None)
            self.app.presence.pop(4, None)

    def test_only_unexpected_closes_are_counted(self):
        counted = []
        self.app.running.update({500: 4, 501: 5})
        self.app.expected_close.add(501)
        with mock.patch.object(av, "roblox_windows", lambda: []), \
                mock.patch.object(self.app.history, "add", lambda now=None, **k: counted.append(k)), \
                mock.patch.object(self.app, "set_taskbar"):
            self.app.tick()
            self.root.after_cancel(self.app.tick_job)
        self.assertEqual(counted, [{"closed": 1}])
        self.assertEqual(self.app.expected_close, set())

    def test_export_payload_has_everything_import_reads(self):
        payload = self.app.export_payload()
        self.assertEqual(len(av.export_entries(payload)), len(self.app.accounts))
        self.assertEqual(payload["accounts"][0]["group"], "Farm")


class TestTransferFile(unittest.TestCase):
    PAYLOAD = {"accounts": [{"id": 1, "name": "alt1", "cookie": FAKE_COOKIE}], "favorites": []}

    def test_aes_gcm_matches_the_nist_vector(self):
        key = bytes.fromhex("feffe9928665731c6d6a8f9467308308" * 2)
        iv = bytes.fromhex("cafebabefacedbaddecaf888")
        aad = bytes.fromhex("feedfacedeadbeeffeedfacedeadbeefabaddad2")
        pt = bytes.fromhex("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"
                           "1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b39")
        ct, tag = av.aes_gcm(True, key, iv, pt, aad)
        self.assertEqual(tag.hex(), "76fc6ece0f4e1768cddf8853bb2d551b")
        self.assertEqual(av.aes_gcm(False, key, iv, ct, aad, tag), pt)
        with self.assertRaises(ValueError):
            av.aes_gcm(False, key, iv, ct, aad, bytes(16))

    def test_round_trip(self):
        data = av.seal_export(self.PAYLOAD, "correct horse")
        self.assertTrue(data.startswith(av.EXPORT_MAGIC))
        self.assertNotIn(FAKE_COOKIE.encode(), data)
        self.assertEqual(av.open_export(data, "correct horse"), self.PAYLOAD)

    def test_wrong_password_or_any_changed_byte_is_refused(self):
        data = av.seal_export(self.PAYLOAD, "correct horse")
        with self.assertRaisesRegex(av.RobloxError, "Wrong password"):
            av.open_export(data, "wrong horse")
        head = len(av.EXPORT_MAGIC) + 1
        for i in (head, head + 20, len(data) - 30, len(data) - 1):   # salt, nonce, body, tag
            bad = bytearray(data)
            bad[i] ^= 1
            with self.assertRaises(av.RobloxError):
                av.open_export(bytes(bad), "correct horse")

    def test_other_files_get_plain_errors(self):
        for data, message in ((b"not an export at all, just some text " * 3, "isn't an AltVault export"),
                              (av.EXPORT_MAGIC + b"\x09" + bytes(60), "newer AltVault")):
            with self.assertRaisesRegex(av.RobloxError, message):
                av.open_export(data, "pw")

    def test_entries_drop_anything_malformed(self):
        entries = av.export_entries({"accounts": [
            {"cookie": FAKE_COOKIE, "name": "alt1", "group": " Farm ", "note": "n", "fps_cap": 15,
             "low_graphics": True, "muted": "yes", "afk_skip": False},
            {"cookie": "short"}, "junk", {"cookie": FAKE_COOKIE + "x", "fps_cap": 9999, "group": 5}]})
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0], {"cookie": FAKE_COOKIE, "name": "alt1", "group": "Farm", "note": "n",
                                      "fps_cap": 15, "low_graphics": True, "muted": False, "afk_skip": False})
        self.assertIsNone(entries[1]["fps_cap"])
        self.assertIsNone(entries[1]["group"])


class TestLiveHelpers(unittest.TestCase):
    def test_grid_fills_the_area_without_scrolling(self):
        for n in range(1, 13):
            cols, rows, w, h = av.live_grid(n, 700, 480, 30, 10, 16 / 10)
            self.assertGreaterEqual(cols * rows, n)
            self.assertLessEqual(cols * w + (cols - 1) * 10, 700)
            self.assertLessEqual(rows * h + (rows - 1) * 10, 480)
        self.assertEqual(av.live_grid(4, 1000, 640, 30, 10, 16 / 10)[:2], (2, 2))
        self.assertEqual(av.live_grid(3, 1400, 300, 30, 10, 16 / 10)[:2], (3, 1))

    def test_fit_keeps_the_shape_and_centres(self):
        self.assertEqual(av.fit_rect(800, 600, 10, 20, 400, 400), (10, 70, 410, 370))
        self.assertEqual(av.fit_rect(0, 0, 5, 5, 10, 10), (5, 5, 15, 15))

    def test_numbers(self):
        self.assertEqual([av.short_number(n) for n in (0, 999, 9999, 12345, 2_500_000, 3e9)],
                         ["0", "999", "9,999", "12.3K", "2.5M", "3B"])

    def test_window_never_bigger_than_the_screen(self):
        with mock.patch.object(av, "work_area", lambda: (1366, 728)):
            self.assertEqual(av.fit_to_screen("1200x740"), "1200x688+83+0")
            self.assertEqual(av.fit_to_screen("1000x500+10+20"), "1000x500+10+20")
            self.assertEqual(av.min_size(1.5), (1366, 728))

    def test_cpu_meter(self):
        meter = av.CpuMeter()
        self.assertIsNone(meter.percent({1: 10.0}, now=100))
        with mock.patch.object(av.os, "cpu_count", lambda: 4):
            self.assertAlmostEqual(meter.percent({1: 12.0, 2: 5.0}, now=102), 25.0)   # 1 of 4 cores


class TestHistory(unittest.TestCase):
    def test_hourly_totals_averages_and_saving(self):
        path = os.path.join(av.APP_DIR, "history-unit.json")
        h = av.History(path)
        now = 1_800_000_000
        h.add(now - 3600, pings=5)
        h.add(now, pings=2, missed=1)
        h.sample(now, pc_cpu=30, open=4)
        h.sample(now, pc_cpu=50, open=4, rbx_cpu=None)
        self.assertEqual(h.series("pings", 3, now), [0, 5, 2])
        self.assertEqual(h.total("pings", 24, now), 7)
        self.assertEqual(h.series("pc_cpu", 2, now, per="pc_cpu_n"), [None, 40])
        self.assertNotIn("rbx_cpu_n", h.hours[h.hour(now)], "a missing reading must not count")
        h.add(now - 3600 * (av.HISTORY_HOURS + 5), pings=99)   # older than a week: dropped on save
        h.save(now)
        again = av.History(path)
        self.assertEqual(again.total("pings", av.HISTORY_HOURS + 10, now), 7)
        with open(path, encoding="utf-8") as f:
            self.assertNotIn("alt", f.read())

    def test_broken_file_starts_empty(self):
        path = os.path.join(av.APP_DIR, "history-broken.json")
        os.makedirs(av.APP_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(av.History(path).hours, {})

    def test_pc_meter(self):
        meter = av.PcMeter()
        self.assertIsNone(meter.percent((100.0, 50.0)))
        self.assertAlmostEqual(meter.percent((103.0, 51.0)), 25.0)   # 1 busy of 4 seconds

    def test_drawn_shapes_are_real_pngs_with_clear_corners(self):
        import tkinter
        png = av.shape_image(20, 20, 6, "#5865f2", check="#ffffff")
        self.assertTrue(png.startswith(b"\x89PNG"))
        root = tkinter.Tk()
        try:
            img = tkinter.PhotoImage(master=root, data=av.base64.b64encode(png))
            self.assertTrue(img.transparency_get(0, 0))
            self.assertFalse(img.transparency_get(10, 3))
        finally:
            root.destroy()




class TestRepoHygiene(unittest.TestCase):
    def test_only_standard_library(self):
        with open(SOURCE, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        modules = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        modules |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        third_party = sorted(m for m in modules if m not in sys.stdlib_module_names and m != "webview2")
        self.assertEqual(third_party, [])
        with open(os.path.join(ROOT, "webview2.py"), encoding="utf-8") as f:   # AltVault's own, also stdlib-only
            tree = ast.parse(f.read())
        modules = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        modules |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertEqual(sorted(m for m in modules if m not in sys.stdlib_module_names), [])

    def test_gitignore_keeps_personal_files_out(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
            ignored = f.read()
        for pattern in ("accounts.dat", "settings.json", "avatars/", "dist/"):
            self.assertIn(pattern, ignored)

    def test_version_is_in_the_changelog(self):
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
            self.assertIn(f"## {av.APP_VERSION}", f.read())


if __name__ == "__main__":
    unittest.main()
