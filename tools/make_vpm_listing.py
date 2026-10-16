# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Build the VPM listing (index.json) that VCC and ALCOM read, from the package zips attached to GitHub releases.

    python tools/make_vpm_listing.py --out site        (in CI: needs GITHUB_REPOSITORY and GITHUB_TOKEN)

On a server of your own, run it from cron and let it write straight into the folder that is served:

    python3 make_vpm_listing.py --repository Krislyz/KAT-Tools --url https://cdn.krislyz.com/vpm/index.json \
        --index-only --out /home/USER/web/cdn.krislyz.com/public_html/vpm

index.json is replaced in one step, and only after GitHub answered, so a failed run leaves the last good one in place.

Each version's entry is the package.json inside its zip, plus the zip's URL and SHA-256, which is what VPM expects.
Standard library only.
"""
import argparse
import hashlib
import io
import json
import os
import tempfile
import urllib.request
import zipfile

NAME = "KAT Tools"
REPO_ID = "com.krislyz.kat.vpm"
AUTHOR = "Krislyz"


def package_json(zip_bytes):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        return json.loads(z.read("package.json"))


def version_key(version):
    """Sort key: 0.2.1 is newer than 0.2.1-beta.2, which is newer than 0.2.1-beta.1, which is newer than 0.2.0."""
    core, _, pre = version.partition("-")
    parts = tuple(int(p) if p.isdigit() else 0 for p in core.split("."))
    return parts + ((1,) if not pre else (0,) + tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split(".")))


def build_listing(entries, listing_url, name=NAME, repo_id=REPO_ID, author=AUTHOR):
    """entries: [(zip_url, zip_bytes)]. The version comes from the package.json inside each zip, never from a file name."""
    packages = {}
    for url, data in entries:
        meta = package_json(data)
        meta = {**meta, "url": url, "zipSHA256": hashlib.sha256(data).hexdigest()}
        packages.setdefault(meta["name"], {"versions": {}})["versions"][meta["version"]] = meta
    for package in packages.values():  # newest first, which is how people read it
        package["versions"] = dict(sorted(package["versions"].items(), key=lambda kv: version_key(kv[0]), reverse=True))
    return {"name": name, "id": repo_id, "url": listing_url, "author": author, "packages": packages}


def fetch(url, token="", accept="application/octet-stream"):
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "kat-vpm"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def release_zips(repository, token):
    """[(download url, bytes)] for every package zip on the repository's releases."""
    api = f"https://api.github.com/repos/{repository}/releases?per_page=100"
    releases = json.loads(fetch(api, token, "application/vnd.github+json"))  # the API refuses octet-stream (HTTP 415)
    out = []
    for release in releases:
        if release.get("draft"):  # a pre-release is listed: VCC and ALCOM only offer it to people who turned pre-releases on
            continue
        for asset in release.get("assets", []):
            if asset["name"].startswith(("com.", "katt-unity-plugin-")) and asset["name"].endswith(".zip"):
                # with a token the API URL is used (private repositories, a higher limit); without one the plain download
                # URL, which is not counted against the 60 requests an hour GitHub gives an unknown caller
                out.append((asset["browser_download_url"], fetch(asset["url"] if token else asset["browser_download_url"], token)))
    return out


def landing_page(listing_url):
    add = f"vcc://vpm/addRepo?url={listing_url}"
    return (f"<!doctype html><meta charset=utf-8><title>{NAME}</title>"
            f"<h1>{NAME}</h1><p>Add this repository to the VRChat Creator Companion or ALCOM:</p>"
            f"<p><a href=\"{add}\">Add to VCC / ALCOM</a></p><p>Or paste this URL into Add Repository:<br><code>{listing_url}</code></p>")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="site")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""), help="owner/name of the GitHub repository")
    parser.add_argument("--url", default="", help="where the listing will be served (default: the repository's GitHub Pages)")
    parser.add_argument("--index-only", action="store_true", help="write index.json and nothing else")
    args = parser.parse_args()
    repository = args.repository
    if "/" not in repository:
        parser.error("give the repository as owner/name, or set GITHUB_REPOSITORY")
    owner, repo = repository.split("/")
    listing_url = args.url or f"https://{owner.lower()}.github.io/{repo}/index.json"
    listing = build_listing(release_zips(repository, os.environ.get("GITHUB_TOKEN", "")), listing_url)
    if not listing["packages"]:
        raise SystemExit("no package zips on the releases: the listing is left as it was")
    os.makedirs(args.out, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=args.out, suffix=".tmp")  # written beside the target and moved over it in one step
    with os.fdopen(handle, "w", encoding="utf-8") as f:
        json.dump(listing, f, indent=2)
    os.chmod(temp, 0o644)
    os.replace(temp, os.path.join(args.out, "index.json"))
    if not args.index_only:
        with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
            f.write(landing_page(listing_url))
    versions = [v for p in listing["packages"].values() for v in p["versions"]]
    print(f"listing written with {len(versions)} version(s): {', '.join(versions)}")


if __name__ == "__main__":
    main()
