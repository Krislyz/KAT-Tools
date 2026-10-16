# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Looking for a newer KAT on GitHub. Pure Python (no bpy), so it runs on the worker thread and in tests."""
import io
import json
import re
import urllib.request
import zipfile

REPO = "Krislyz/KAT-Tools"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOADS = f"https://github.com/{REPO}/releases/download/"  # the only place a zip is ever fetched from
EXT_ID = "kat"
MAX_BYTES = 64 << 20


def parse(text):
    """'v0.2.2' -> (0, 2, 2); None when it is not a plain release number. Pre-releases never count as newer."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(text).strip())
    return tuple(int(x) for x in m.groups()) if m else None


def blender_asset(release):
    """(name, url) of the Blender extension zip on a release, or None."""
    for asset in release.get("assets", []):
        name, url = asset.get("name", ""), asset.get("browser_download_url", "")
        if name.endswith(".zip") and re.fullmatch(r"katt-blender-plugin-v[\d.]+\.zip|kat-\d[\d.]*\.zip", name) and url.startswith(DOWNLOADS):
            return name, url
    return None


def newer(release, current):
    """{'version', 'name', 'url', 'page'} when `release` is newer than `current`, else None."""
    latest, mine, asset = parse(release.get("tag_name", "")), parse(current), blender_asset(release)
    if not (latest and mine and asset and latest > mine) or release.get("draft") or release.get("prerelease"):
        return None
    return dict(version=".".join(map(str, latest)), name=asset[0], url=asset[1], page=release.get("html_url", ""))


def fetch(url, accept="*/*", limit=MAX_BYTES):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "KAT-Blender-plugin"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("download too large")
    return data


def check(current):
    """The newer release, or None. Raises on network errors: the caller decides whether to tell anyone."""
    return newer(json.loads(fetch(API, "application/vnd.github+json", 1 << 20)), current)


def download(info):
    """The zip's bytes, after making sure it is KAT's extension of the version promised."""
    if not info["url"].startswith(DOWNLOADS):
        raise ValueError("not a KAT release link")
    data = fetch(info["url"])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        import tomllib
        manifest = tomllib.loads(z.read("blender_manifest.toml").decode("utf-8"))
    if manifest.get("id") != EXT_ID or parse(manifest.get("version", "")) != parse(info["version"]):
        raise ValueError("the zip is not KAT " + info["version"])
    return data
