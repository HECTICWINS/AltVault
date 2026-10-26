"""Checks a built release zip before it's published:

    py -3 tests/check_release.py dist/AltVault-<version>-win64.zip

Fails if the zip contains anything personal to the build machine (its username, home folder or
build path), any of AltVault's data files, or an app file that differs from the repo.
"""
import io
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILES = re.compile(r"(^|/)(accounts\.dat|settings\.json|.*\.log|avatars/|.*\.bak|.*\.tmp)", re.I)
APP_FILES = ("altvault.py", "README.md", "LICENSE")


def personal_strings():
    found = {os.path.expanduser("~"), ROOT}
    for var in ("USERNAME", "USERPROFILE", "COMPUTERNAME"):
        if os.environ.get(var):
            found.add(os.environ[var])
    found = {s for s in found if len(s) >= 4}
    # Paths can appear with either slash and in any case.
    return [re.compile(re.escape(s).replace(r"\\", r"[\\/]+").encode(), re.I) for s in found]


def entries(z, prefix=""):
    for name in z.namelist():
        data = z.read(name)
        yield prefix + name, data
        if name.endswith(".zip"):
            yield from entries(zipfile.ZipFile(io.BytesIO(data)), prefix + name + ":")


def main(path):
    problems = []
    patterns = personal_strings()
    with zipfile.ZipFile(path) as z:
        dates = {info.date_time for info in z.infolist()}
        if len(dates) != 1:
            problems.append(f"zip entries carry {len(dates)} different dates; expected one fixed date")
        for name, data in entries(z):
            if DATA_FILES.search(name):
                problems.append(f"data file in the release: {name}")
            for p in patterns:
                if p.search(data):
                    problems.append(f"{name} contains build-machine text matching {p.pattern.decode()!r}")
        for app_file in APP_FILES:
            with open(os.path.join(ROOT, app_file), "rb") as f:
                if z.read(f"AltVault/{app_file}") != f.read():
                    problems.append(f"AltVault/{app_file} differs from the repo's copy")
    for p in problems:
        print("FAIL:", p)
    print(f"{path}: {'OK' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
