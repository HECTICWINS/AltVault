"""Builds the portable AltVault download, so users don't need to install Python.

Run it with the official python.org Python you want to ship (3.9+, 64-bit):

    py -3.13 build_release.py

Output:
    dist/AltVault/                  the unzip-and-run folder (double-click AltVault.exe)
    dist/AltVault-<ver>-win64.zip   the file to upload to GitHub Releases

Every binary is copied unchanged from the python.org install, so each keeps its Python Software
Foundation (or Microsoft) signature and Smart App Control / SmartScreen treat it as trusted.
AltVault.exe is pythonw.exe renamed; renaming a file doesn't affect its signature. Nothing is
compiled into a new unsigned .exe, which is what gets PyInstaller-style apps blocked or flagged.
"""
import os
import py_compile
import re
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(DIST, "AltVault")
BASE = sys.base_prefix
VER = f"{sys.version_info.major}{sys.version_info.minor}"
APP_FILES = ["altvault.py", "README.md", "LICENSE"]

# Parts of Python that AltVault never uses. Leaving them out keeps the download small.
SKIP_LIB = {"test", "idlelib", "ensurepip", "site-packages", "turtledemo", "pydoc_data", "venv",
            "lib2to3", "__phello__", "sqlite3", "curses", "dbm", "unittest", "msilib", "distutils"}
SKIP_DLL_PREFIX = ("_test", "_ctypes_test", "_sqlite3", "sqlite3", "winsound")
SKIP_TCL = {"demos", "images", "nmake", "dde1.4", "reg1.3", "tzdata"}   # tzdata: Tcl's clock zones

# Opens AltVault when AltVault.exe is started with no arguments. Python imports sitecustomize
# while starting up, before it would otherwise sit idle with nothing to run.
SITECUSTOMIZE = '''\
"""Starts AltVault when AltVault.exe (Python's own signed pythonw.exe, renamed) is opened."""
import os
import sys

if sys.argv in ([], [""]):
    try:
        import altvault
        altvault.main()
    except BaseException:
        import ctypes
        import traceback
        ctypes.windll.user32.MessageBoxW(None, "AltVault couldn't start:\\n\\n" + traceback.format_exc(),
                                         "AltVault", 0x10)
    finally:
        import logging
        logging.shutdown()
        os._exit(0)
'''


# Every zip entry gets this date, so the download doesn't reveal when or where it was built
# (zip times are the builder's local clock).
ZIP_DATE = (2026, 1, 1, 0, 0, 0)


def zip_add(z, src, arcname):
    info = zipfile.ZipInfo(arcname, ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    with open(src, "rb") as f:
        z.writestr(info, f.read(), compresslevel=9)


def copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def build_stdlib_zip(path):
    """Compiles the needed standard library into one zip of .pyc files. Each file records a neutral
    'Lib/...' path rather than where Python is installed, so the build machine's folders (and its
    Windows username) never end up in the download."""
    lib = os.path.join(BASE, "Lib")
    tmp = os.path.join(DIST, "_build.pyc")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, dirs, files in os.walk(lib):
            rel_dir = os.path.relpath(folder, lib).replace("\\", "/")
            top = rel_dir.split("/")[0]
            if rel_dir != "." and (top in SKIP_LIB or not os.path.isfile(os.path.join(folder, "__init__.py"))):
                dirs[:] = []
                continue
            dirs[:] = sorted(d for d in dirs if d not in ("__pycache__", "test", "tests"))
            for name in sorted(files):
                if not name.endswith(".py") or (rel_dir == "." and name[:-3] in SKIP_LIB):
                    continue
                rel = name if rel_dir == "." else f"{rel_dir}/{name}"
                py_compile.compile(os.path.join(folder, name), cfile=tmp, dfile="Lib/" + rel, optimize=2,
                                   doraise=True, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
                zip_add(z, tmp, rel + "c")
    os.remove(tmp)


def main():
    if sys.platform != "win32" or sys.maxsize < 2**32:
        sys.exit("Build with 64-bit python.org Python on Windows.")
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)

    copy(os.path.join(BASE, "pythonw.exe"), os.path.join(OUT, "AltVault.exe"))
    for name in (f"python{VER}.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        copy(os.path.join(BASE, name), os.path.join(OUT, name))
    copy(os.path.join(BASE, "LICENSE.txt"), os.path.join(OUT, "LICENSE-Python.txt"))

    dlls = os.path.join(BASE, "DLLs")
    for name in os.listdir(dlls):
        if name.endswith((".pyd", ".dll")) and not name.startswith(SKIP_DLL_PREFIX):
            copy(os.path.join(dlls, name), os.path.join(OUT, "DLLs", name))

    tcl = os.path.join(BASE, "tcl")
    for name in os.listdir(tcl):
        if name not in SKIP_TCL and os.path.isdir(os.path.join(tcl, name)):
            shutil.copytree(os.path.join(tcl, name), os.path.join(OUT, "tcl", name),
                            ignore=shutil.ignore_patterns(*SKIP_TCL))

    build_stdlib_zip(os.path.join(OUT, f"python{VER}.zip"))
    # A ._pth named after the exe makes Python use exactly these paths and ignore PYTHONPATH,
    # PYTHONHOME and user site-packages, so nothing on the PC can inject code into AltVault.
    with open(os.path.join(OUT, "AltVault._pth"), "w", encoding="utf-8") as f:
        f.write(f"python{VER}.zip\nDLLs\n.\nimport site\n")
    with open(os.path.join(OUT, "sitecustomize.py"), "w", encoding="utf-8") as f:
        f.write(SITECUSTOMIZE)
    for name in APP_FILES:
        copy(os.path.join(ROOT, name), os.path.join(OUT, name))

    with open(os.path.join(ROOT, "altvault.py"), encoding="utf-8") as f:
        m = re.search(r'^APP_VERSION = "([^"]+)"', f.read(), re.M)
    version = m.group(1) if m else "dev"
    archive = os.path.join(DIST, f"AltVault-{version}-win64.zip")
    if os.path.exists(archive):
        os.remove(archive)
    with zipfile.ZipFile(archive, "w") as z:
        for folder, dirs, files in os.walk(OUT):
            dirs.sort()
            for name in sorted(files):
                full = os.path.join(folder, name)
                zip_add(z, full, os.path.relpath(full, DIST).replace("\\", "/"))
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(OUT) for f in fs)
    print(f"Built {OUT} ({size / 2**20:.1f} MB unpacked)")
    print(f"      {archive} ({os.path.getsize(archive) / 2**20:.1f} MB)")


if __name__ == "__main__":
    main()
