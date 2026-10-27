"""Builds the keyless version of AltVault from this (keyed) source tree.

    py -3 tools/make_free_build.py [OUTPUT_DIR]       default: ../AltVault-free next to this repo

Everything between ">>> keyed" and "<<< keyed" marker lines is removed (the access-key check, the
activation window, the start-up gate, their tests, and README/CHANGELOG notes about keys), and the
key-making tools are left out. Nothing else changes, so the two versions never drift apart: fix a
bug here, run this again, and the keyless copy has the same fix.
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "dev", "dist", "tools", "__pycache__", ".claude"}
SKIP_FILES = {"license.key", "my-pc-key.txt"}
TEXT = {".py", ".md", ".yml", ".yaml", ".txt", ""}


def strip_keyed(text):
    """The text without the lines from a '>>> keyed' marker to its '<<< keyed' marker, inclusive."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        if ">>> keyed" in line:
            skipping = True
        if not skipping:
            out.append(line)
        if "<<< keyed" in line:
            skipping = False
    if skipping:
        raise ValueError("a '>>> keyed' marker has no matching '<<< keyed'")
    return "".join(out)


def build(out_dir):
    out_dir = os.path.abspath(out_dir)
    if os.path.commonpath([out_dir, ROOT]) == ROOT:
        raise ValueError("the output folder can't be inside this repository")
    for name in os.listdir(out_dir) if os.path.isdir(out_dir) else ():
        if name != ".git":   # keep a git repo's history, replace everything else
            target = os.path.join(out_dir, name)
            shutil.rmtree(target) if os.path.isdir(target) else os.remove(target)
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name in SKIP_FILES or name.endswith((".pyc", ".bak", ".blob")):
                continue
            src = os.path.join(base, name)
            dst = os.path.join(out_dir, os.path.relpath(src, ROOT))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.splitext(name)[1] in TEXT:
                with open(src, encoding="utf-8", newline="") as f:
                    text = f.read()
                with open(dst, "w", encoding="utf-8", newline="") as f:
                    f.write(strip_keyed(text))
            else:
                shutil.copy2(src, dst)
    leftovers = [name for base, _, files in os.walk(out_dir) if ".git" not in base.split(os.sep) for name in files
                 for text in [open(os.path.join(base, name), encoding="utf-8", errors="ignore").read()]
                 if re.search(r"LICENSE_REQUIRED|ActivationWindow|license_tool|AVK-|ECCPRIVATEBLOB", text)]
    if leftovers:
        raise ValueError(f"key code is still present in: {leftovers}")
    return out_dir


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(ROOT), "AltVault-free")
    print("Keyless build written to", build(target))
