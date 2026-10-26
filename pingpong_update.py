"""Self-update for the PingPong lamp (n1mm_lamps.exe).

Releases come from GitHub (s55oo/N1MMPingPong). Installing a version:

1. download the release's n1mm_lamps.exe next to the running exe as
   "<name>.download", check its size and SHA-256 against the release;
2. rename the running exe to "<name>.v<current>.exe" (Windows allows
   renaming a running exe) - this is the backup used by "Revert";
3. move the download into place and start it; the old process exits.

Only one backup is kept. Settings live in pingpong.ini next to the exe.
Updating works only for the frozen exe, not when running from source.
"""

import configparser
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO = "s55oo/N1MMPingPong"
API_URL = "https://api.github.com/repos/{}/releases".format(REPO)
ASSET_NAME = "n1mm_lamps.exe"
SETTINGS_FILE = "pingpong.ini"
TIMEOUT = 20


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def exe_path():
    return os.path.abspath(sys.executable)


def parse_version(text):
    """'v1.10' -> (1, 10); returns () when there is no number."""
    return tuple(int(n) for n in re.findall(r"\d+", text or ""))


def _request(url):
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": "PingPong-updater",
            "Accept": "application/vnd.github+json",
        },
    )


def fetch_releases():
    """Published releases that carry the exe, newest version first.

    Each item: {"version", "tag", "url", "size", "sha256", "date"}.
    Raises OSError / ValueError on network or format problems.
    """
    with urllib.request.urlopen(_request(API_URL), timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    releases = []
    for rel in data:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name") or ""
        if not parse_version(tag):
            continue
        for asset in rel.get("assets") or []:
            if asset.get("name") != ASSET_NAME:
                continue
            digest = asset.get("digest") or ""
            releases.append(
                {
                    "version": tag.lstrip("vV"),
                    "tag": tag,
                    "url": asset.get("browser_download_url"),
                    "size": asset.get("size") or 0,
                    "sha256": digest[7:].lower()
                    if digest.startswith("sha256:")
                    else "",
                    "date": (rel.get("published_at") or "")[:10],
                }
            )
            break
    releases.sort(key=lambda r: parse_version(r["version"]), reverse=True)
    return releases


def newer_release(releases, current):
    if releases and parse_version(releases[0]["version"]) > parse_version(current):
        return releases[0]
    return None


def download(release, target_exe, progress=None):
    """Download and verify; returns the path of the verified file."""
    part = target_exe + ".download"
    sha = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(_request(release["url"]), timeout=TIMEOUT) as resp, open(
            part, "wb"
        ) as out:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, release["size"])
        if release["size"] and done != release["size"]:
            raise ValueError(
                "download incomplete ({} of {} bytes)".format(done, release["size"])
            )
        if release["sha256"] and sha.hexdigest() != release["sha256"]:
            raise ValueError("checksum mismatch - download corrupted")
        with open(part, "rb") as fh:
            if fh.read(2) != b"MZ":
                raise ValueError("downloaded file is not a Windows program")
    except Exception:
        _remove(part)
        raise
    return part


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _stem(target_exe):
    return os.path.splitext(target_exe)[0]


def backup_path(target_exe, version):
    return "{}.v{}.exe".format(_stem(target_exe), version)


def find_backup(target_exe):
    """(version, path) of the backup kept by the last switch, or None."""
    found = []
    for path in glob.glob(glob.escape(_stem(target_exe)) + ".v*.exe"):
        match = re.search(r"\.v([\d.]+)\.exe$", path, re.IGNORECASE)
        if match:
            found.append((os.path.getmtime(path), match.group(1), path))
    if not found:
        return None
    found.sort()
    return found[-1][1], found[-1][2]


def _drop_old_backups(target_exe, keep=None):
    for path in glob.glob(glob.escape(_stem(target_exe)) + ".v*.exe"):
        if keep is None or os.path.normcase(path) != os.path.normcase(keep):
            _remove(path)


def swap_in(target_exe, current_version, new_file):
    """Put new_file in place of target_exe, keeping the current as backup.

    Works while target_exe is running. new_file is consumed (moved).
    """
    backup = backup_path(target_exe, current_version)
    _drop_old_backups(target_exe)
    os.replace(target_exe, backup)
    try:
        os.replace(new_file, target_exe)
    except OSError:
        os.replace(backup, target_exe)
        raise
    # Keep the backup's date = time of the switch, so it is "the previous".
    try:
        os.utime(backup, None)
    except OSError:
        pass
    return backup


def revert(target_exe, current_version):
    """Swap the running exe with the kept backup. Returns backup version."""
    found = find_backup(target_exe)
    if not found:
        raise OSError("no previous version is kept in this folder")
    version, path = found
    tmp = target_exe + ".revert"
    os.replace(path, tmp)
    try:
        swap_in(target_exe, current_version, tmp)
    except OSError:
        os.replace(tmp, path)
        raise
    return version


def relaunch(target_exe, args):
    """Start target_exe as a fresh, independent process."""
    env = dict(os.environ)
    # PyInstaller >= 6.9: make the child unpack itself instead of reusing
    # this (soon deleted) process's temp folder.
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for key in list(env):
        if key.startswith("_PYI_") or key == "_MEIPASS2":
            env.pop(key)
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen(
        [target_exe] + list(args),
        env=env,
        cwd=os.path.dirname(target_exe),
        close_fds=True,
        creationflags=flags,
    )


def cleanup_leftovers(target_exe):
    for suffix in (".download", ".revert"):
        _remove(target_exe + suffix)


class Settings:
    def __init__(self, folder):
        self.path = os.path.join(folder, SETTINGS_FILE)
        self.cfg = configparser.ConfigParser()
        try:
            self.cfg.read(self.path, encoding="utf-8")
        except (OSError, configparser.Error):
            pass
        if not self.cfg.has_section("update"):
            self.cfg.add_section("update")

    def get(self, key, default):
        try:
            return self.cfg.getboolean("update", key, fallback=default)
        except ValueError:
            return default

    def set(self, key, value):
        self.cfg.set("update", key, "yes" if value else "no")
        self._save()

    def get_text(self, section, key, default=""):
        return self.cfg.get(section, key, fallback=default)

    def set_text(self, section, key, value):
        if not self.cfg.has_section(section):
            self.cfg.add_section(section)
        self.cfg.set(section, key, value)
        self._save()

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                self.cfg.write(fh)
        except OSError:
            pass
