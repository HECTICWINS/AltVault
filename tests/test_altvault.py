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
                                     join_finished=finished.append, remember_place=lambda c, p: None)
        for name in ("join_worker", "resolve_private", "wait_for_new_window", "fps_caps", "fps_cap_of",
                     "apply_fps_cap", "restore_fps_cap"):
            setattr(fake, name, types.MethodType(getattr(av.App, name), fake))
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

    def test_private_link_is_never_saved(self):
        saved = []
        fake = types.SimpleNamespace(
            settings={"place": "https://www.roblox.com/games/1/Public"},
            place_var=types.SimpleNamespace(get=lambda: "https://www.roblox.com/share?code=SECRET123&type=Server"),
            **{name: types.SimpleNamespace(get=lambda: True)
               for name in ("multi_var", "same_server_var", "auto_tile_var", "anti_afk_var")})
        with mock.patch.object(av, "save_settings", lambda s: saved.append(dict(s))):
            av.App.save_settings(fake)
        self.assertNotIn("SECRET123", repr(saved))
        self.assertEqual(saved[-1]["place"], "https://www.roblox.com/games/1/Public")


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
        for name in ("fps_caps", "fps_cap_of", "apply_fps_cap", "restore_fps_cap"):
            setattr(fake, name, types.MethodType(getattr(av.App, name), fake))
        return fake

    def test_each_account_starts_with_its_own_cap(self):
        app = self.make_app({"1": 20, "3": 45})
        seen = []
        with mock.patch.object(av, "save_settings", lambda s: None):
            for acc_id in (1, 2, 3):
                app.apply_fps_cap(self.path, {"id": acc_id})
                seen.append(av.read_fps_cap(self.path))     # what this client would start with
                app.restore_fps_cap(self.path)
                self.assertEqual(av.read_fps_cap(self.path), 60, "your own setting must come back")
        self.assertEqual(seen, [20, 60, 45])
        self.assertEqual(app.settings["fps_own"], 60)

    def test_a_closing_alt_cant_overwrite_your_own_setting(self):
        av.write_fps_cap(self.path, 20)          # an alt saved its cap when it closed
        app = self.make_app({"1": 20}, fps_own=60, fps_written=60)
        with mock.patch.object(av, "save_settings", lambda s: None):
            app.apply_fps_cap(self.path, {"id": 2})
        self.assertEqual(app.settings["fps_own"], 60)
        self.assertEqual(av.read_fps_cap(self.path), 60)

    def test_your_in_game_change_is_respected(self):
        av.write_fps_cap(self.path, 144)         # you picked 144 in Roblox's menu
        app = self.make_app({"1": 20}, fps_own=60, fps_written=60)
        with mock.patch.object(av, "save_settings", lambda s: None):
            app.apply_fps_cap(self.path, {"id": 1})
            self.assertEqual(av.read_fps_cap(self.path), 20)
            app.restore_fps_cap(self.path)
        self.assertEqual(app.settings["fps_own"], 144)
        self.assertEqual(av.read_fps_cap(self.path), 144)

    def test_invalid_caps_are_ignored(self):
        app = self.make_app({"1": 20, "2": 0, "3": 9999, "4": "30", "5": None, "6": 240, "7": 5})
        self.assertEqual(app.fps_caps(), {"1": 20, "6": 240, "7": 5})
        self.assertEqual(self.make_app("garbage").fps_caps(), {})

    def test_join_applies_caps_in_order(self):
        events = []
        windows = []
        accounts = [{"id": i, "name": f"alt{i}", "cookie": f"cookie{i}"} for i in (1, 2, 3)]
        fake = types.SimpleNamespace(expired=set(), running={}, presence={}, ui=lambda fn, *a: fn(*a),
                                     settings={"fps_caps": {"1": 20, "3": 45}},
                                     set_status=lambda text: None, join_finished=lambda p: events.append(("done", p)),
                                     remember_place=lambda c, p: None, wait_for_server=lambda *a: None)
        for name in ("join_worker", "wait_for_new_window", "fps_caps", "fps_cap_of",
                     "apply_fps_cap", "restore_fps_cap"):
            setattr(fake, name, types.MethodType(getattr(av.App, name), fake))

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

        def fake_pass(hwnds, vk):
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
            return pinged, [], missed, reasons

        timer = [None]
        fake = types.SimpleNamespace(
            root=types.SimpleNamespace(after=lambda ms, fn: timer.__setitem__(0, (clock[0] + ms / 1000, fn)) or 1,
                                       after_cancel=lambda job: timer.__setitem__(0, None)),
            anti_afk_var=types.SimpleNamespace(get=lambda: True), settings={}, running={}, joining=False,
            afk_busy=False, afk_busy_since=0, afk_pass_id=0, afk_wait_started=None, afk_waiting_for=None,
            afk_waiting_since=0, afk_waiting_logged=False, afk_last={}, afk_misses={}, afk_job=None,
            status=types.SimpleNamespace(config=lambda **k: None), account=lambda i: None)
        for name in ("schedule_afk", "afk_skip_ids", "afk_due", "_afk_due", "_afk_waiting",
                     "afk_finished", "afk_next_delay", "afk_window_name"):
            setattr(fake, name, types.MethodType(getattr(av.App, name), fake))
        fake.ui = lambda fn, *a: fn(*a)

        user32 = types.SimpleNamespace(IsIconic=lambda h: False, GetWindowThreadProcessId=lambda h, p: None)
        with mock.patch.object(av.time, "monotonic", lambda: clock[0]), \
                mock.patch.object(av, "roblox_windows", lambda: [(h, p, "Roblox") for h, p in windows.items()]), \
                mock.patch.object(av, "idle_seconds", lambda: 0.0 if rnd.random() < busy_user else 10.0), \
                mock.patch.object(av, "_user32", user32), \
                mock.patch.object(av, "anti_afk_pass", fake_pass), \
                mock.patch.object(av.random, "uniform", rnd.uniform), \
                mock.patch.object(av.threading, "Thread", lambda target, daemon: types.SimpleNamespace(start=target)), \
                mock.patch.object(av.log, "info"):
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
        self.assertLess(healthy / 60, 12, "a healthy window went too long without a ping")
        self.assertLess(flaky / 60, 17, "a window that often misses went too long without a ping")

    def test_calm_conditions_ping_on_schedule(self):
        worst = self.simulate(1, flaky=0, busy_user=0)
        self.assertLess(max(worst.values()) / 60, 9.5)


class TestRepoHygiene(unittest.TestCase):
    def test_only_standard_library(self):
        with open(SOURCE, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        modules = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        modules |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        third_party = sorted(m for m in modules if m not in sys.stdlib_module_names)
        self.assertEqual(third_party, [])

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
