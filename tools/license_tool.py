"""The maker's tool for AltVault keys. It is not part of the app and holds no secret itself.

    py -3 tools/license_tool.py init                    make the signing key (once) and print the public key
    py -3 tools/license_tool.py make "ABCD-EFGH-..."    make a key for the PC code a user sent you
    py -3 tools/license_tool.py make CODE --days 30     ...that stops working after 30 days (default: never)
    py -3 tools/license_tool.py make CODE --name Sam    ...and note who it's for in your ledger
    py -3 tools/license_tool.py make CODE --copy        ...and put the key on your clipboard, ready to paste into a message
    py -3 tools/license_tool.py list                    everyone you've made a key for

Every key you make is noted in issued-keys.csv next to the signing key (never in the repo), so you can
see who you approved. A key can't be cancelled once sent, so approve with care.

The private signing key is saved OUTSIDE this repository (default: ~/Tools/AltVault-signing) and
readable only by you. Whoever holds it can make keys, so never put it in a repo, a chat or a cloud
folder, and keep a backup somewhere private: if it's lost, no new keys can be made for the app
that carries its public key. A key only works on the PC it was made for.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import datetime
import hashlib
import importlib.util
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Tools", "AltVault-signing")
KEY_FILE = "signing-key.blob"
LEDGER = "issued-keys.csv"

_bcrypt = ctypes.WinDLL("bcrypt")
for name, argtypes in {
    "BCryptOpenAlgorithmProvider": [ctypes.POINTER(ctypes.c_void_p), wt.LPCWSTR, wt.LPCWSTR, wt.ULONG],
    "BCryptGenerateKeyPair": [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), wt.ULONG, wt.ULONG],
    "BCryptFinalizeKeyPair": [ctypes.c_void_p, wt.ULONG],
    "BCryptExportKey": [ctypes.c_void_p, ctypes.c_void_p, wt.LPCWSTR, ctypes.c_char_p, wt.ULONG,
                        ctypes.POINTER(wt.ULONG), wt.ULONG],
    "BCryptImportKeyPair": [ctypes.c_void_p, ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                            ctypes.c_char_p, wt.ULONG, wt.ULONG],
    "BCryptSignHash": [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, wt.ULONG, ctypes.c_char_p, wt.ULONG,
                       ctypes.POINTER(wt.ULONG), wt.ULONG],
    "BCryptDestroyKey": [ctypes.c_void_p],
    "BCryptCloseAlgorithmProvider": [ctypes.c_void_p, wt.ULONG],
}.items():
    fn = getattr(_bcrypt, name)
    fn.argtypes, fn.restype = argtypes, ctypes.c_long


def _check(status, what):
    if status:
        raise OSError(f"{what} failed (0x{status & 0xFFFFFFFF:08X})")


def _altvault():
    """The app module, so keys are made in exactly the format the app checks."""
    os.environ.setdefault("LOCALAPPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Local"))
    spec = importlib.util.spec_from_file_location("altvault", os.path.join(HERE, "..", "altvault.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def new_keypair():
    """(private_blob, public_blob) for a fresh ECDSA P-256 key."""
    alg, key = ctypes.c_void_p(), ctypes.c_void_p()
    _check(_bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(alg), "ECDSA_P256", None, 0), "open")
    try:
        _check(_bcrypt.BCryptGenerateKeyPair(alg, ctypes.byref(key), 256, 0), "generate")
        _check(_bcrypt.BCryptFinalizeKeyPair(key, 0), "finalize")
        blobs = []
        for kind in ("ECCPRIVATEBLOB", "ECCPUBLICBLOB"):
            size = wt.ULONG()
            _check(_bcrypt.BCryptExportKey(key, None, kind, None, 0, ctypes.byref(size), 0), "size")
            buf = ctypes.create_string_buffer(size.value)
            _check(_bcrypt.BCryptExportKey(key, None, kind, buf, size.value, ctypes.byref(size), 0), "export")
            blobs.append(buf.raw[:size.value])
        _bcrypt.BCryptDestroyKey(key)
        return tuple(blobs)
    finally:
        _bcrypt.BCryptCloseAlgorithmProvider(alg, 0)


def sign(private_blob, digest):
    """A 64-byte signature (r then s) of a 32-byte digest."""
    alg, key = ctypes.c_void_p(), ctypes.c_void_p()
    _check(_bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(alg), "ECDSA_P256", None, 0), "open")
    try:
        _check(_bcrypt.BCryptImportKeyPair(alg, None, "ECCPRIVATEBLOB", ctypes.byref(key), private_blob,
                                           len(private_blob), 0), "import")
        try:
            out, size = ctypes.create_string_buffer(64), wt.ULONG()
            _check(_bcrypt.BCryptSignHash(key, None, digest, len(digest), out, 64, ctypes.byref(size), 0), "sign")
            return out.raw[:size.value]
        finally:
            _bcrypt.BCryptDestroyKey(key)
    finally:
        _bcrypt.BCryptCloseAlgorithmProvider(alg, 0)


def make_key(private_blob, pc_text, days=None, today=None, app=None):
    """The key text for the PC code `pc_text`. days=None means it never expires."""
    app = app or _altvault()
    pc = app.unb32(pc_text)
    if len(pc) != 10:
        raise ValueError("That isn't a PC code (it should look like ABCD-EFGH-JKLM-NPQR).")
    expires = 0
    if days:
        expires = int((today or datetime.date.today()).__add__(datetime.timedelta(days=days)).strftime("%Y%m%d"))
    digest = hashlib.sha256(app.license_message(pc, expires)).digest()
    return f"{app.KEY_PREFIX}-" + app.group(app.b32(struct.pack(">I", expires) + sign(private_blob, digest)), 5)


def _private_only(path):
    """Leaves the file readable by you only (and SYSTEM)."""
    import subprocess
    sid = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True).stdout
    sid = sid.strip().strip('"').split('","')[-1].strip('"')
    subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", f"*{sid}:F", "*S-1-5-18:F"],
                   capture_output=True)


def find_pc_code(text):
    """The PC code inside whatever someone sent you (their whole message, or just the code), or None."""
    found = re.search(r"\b[A-Za-z2-7]{4}(?:-[A-Za-z2-7]{4}){3}\b", text or "")
    return found.group(0).upper() if found else None


def issue_key(directory, pc_code, name="", days=None, app=None):
    """Makes a key with the signing key in `directory`, notes it in the ledger there, and returns it."""
    path = os.path.join(directory, KEY_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError(f"No signing key at {path}. Run:  py -3 tools/license_tool.py init")
    with open(path, "rb") as f:
        key = make_key(f.read(), pc_code, days, app=app)
    ledger = os.path.join(directory, LEDGER)
    first = not os.path.exists(ledger)
    with open(ledger, "a", encoding="utf-8") as f:
        if first:
            f.write("date,name,pc_code,days" + chr(10))
        row = f"{datetime.date.today()},{name.replace(',', ' ')},{pc_code},{days or 'never expires'}"
        f.write(row + chr(10))
    return key


def read_ledger(directory):
    ledger = os.path.join(directory, LEDGER)
    if not os.path.exists(ledger):
        return "No keys made yet."
    with open(ledger, encoding="utf-8") as f:
        return f.read()


def copy_to_clipboard(text):
    import subprocess
    subprocess.run(["clip"], input=text.encode("utf-16le"), check=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="make the signing key (once)")
    make = sub.add_parser("make", help="make a key for a PC code")
    make.add_argument("pc_code")
    make.add_argument("--days", type=int, help="stops working after this many days (default: never)")
    make.add_argument("--name", default="", help="who the key is for, for your own records")
    make.add_argument("--copy", action="store_true", help="copy the key to the clipboard")
    listing = sub.add_parser("list", help="show the keys you've made")
    for p in (init, make, listing):
        p.add_argument("--dir", default=DEFAULT_DIR, help="where the private signing key lives")
    args = parser.parse_args(argv)
    path = os.path.join(args.dir, KEY_FILE)
    if args.command == "init":
        if os.path.exists(path):
            sys.exit(f"A signing key already exists at {path}. Not overwriting it: that would make every "
                     "key you've handed out stop matching new builds.")
        os.makedirs(args.dir, exist_ok=True)
        private_blob, public_blob = new_keypair()
        with open(path, "wb") as f:
            f.write(private_blob)
        _private_only(path)
        print(f"Private signing key saved to {path}\nKeep a private backup, and never put it in a repository.\n")
        print("Public key (paste into LICENSE_PUBLIC_KEY in altvault.py):\n" + public_blob.hex())
    elif args.command == "list":
        print(read_ledger(args.dir))
    else:
        code = find_pc_code(args.pc_code) or args.pc_code
        try:
            key = issue_key(args.dir, code, args.name, args.days)
        except (FileNotFoundError, ValueError) as e:
            sys.exit(str(e))
        print(key)
        if args.copy:
            copy_to_clipboard(key)
            print("(copied to your clipboard)")


if __name__ == "__main__":
    main()
