# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Update checker logic, no network. Run: blender -b --factory-startup --python tests/test_update.py"""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("kat_update", os.path.join(HERE, "..", "kat", "core", "update.py"))
up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(up)

URL = up.DOWNLOADS + "v0.3.0/kats-blender-plugin-v0.3.0.zip"


def release(tag="v0.3.0", url=URL, **extra):
    return dict(tag_name=tag, html_url="page", assets=[
        dict(name="kat-unity-plugin-v0.3.0.zip", browser_download_url=up.DOWNLOADS + "v0.3.0/kat-unity-plugin-v0.3.0.zip"),
        dict(name="kats-blender-plugin-v0.3.0.zip", browser_download_url=url)], **extra)


class UpdateTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(up.parse("v0.10.2"), (0, 10, 2))
        self.assertIsNone(up.parse("0.3.0-beta.1"))

    def test_newer_picks_the_blender_zip(self):
        info = up.newer(release(), "0.2.2")
        self.assertEqual((info["version"], info["url"]), ("0.3.0", URL))

    def test_not_newer(self):
        for current in ("0.3.0", "0.10.0"):
            self.assertIsNone(up.newer(release(), current))

    def test_ignores_prereleases_drafts_and_foreign_links(self):
        self.assertIsNone(up.newer(release(prerelease=True), "0.2.2"))
        self.assertIsNone(up.newer(release(draft=True), "0.2.2"))
        self.assertIsNone(up.newer(release(url="https://evil.test/kats-blender-plugin-v0.3.0.zip"), "0.2.2"))

    def test_old_zip_name_still_found(self):
        r = dict(tag_name="v0.3.0", assets=[dict(name="kat-0.3.0.zip", browser_download_url=up.DOWNLOADS + "v0.3.0/kat-0.3.0.zip")])
        self.assertEqual(up.newer(r, "0.2.2")["name"], "kat-0.3.0.zip")


if __name__ == "__main__":
    result = unittest.main(argv=["x"], exit=False, verbosity=0).result
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("UPDATE OK")
