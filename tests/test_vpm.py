# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Run: blender -b --factory-startup --python tests/test_vpm.py"""
import hashlib
import importlib.util
import io
import json
import os
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("kat_vpm", os.path.join(HERE, "..", "tools", "make_vpm_listing.py"))
vpm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vpm)
PACKAGE_DIR = os.path.join(HERE, "..", "unity", "com.krislyz.kat")


def make_zip(version, name="com.krislyz.kat"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("package.json", json.dumps({"name": name, "version": version, "displayName": "KAT", "unity": "2022.3"}))
        z.writestr("Editor/x.cs", "// code")
    return buffer.getvalue()


class ListingTest(unittest.TestCase):
    def test_asmdef_version_expressions_are_ones_unity_accepts(self):
        # Unity rejects an interval with an empty end such as "[3.0.0,)": a minimum version is just "3.0.0"
        import glob
        for path in glob.glob(os.path.join(PACKAGE_DIR, "Editor", "**", "*.asmdef"), recursive=True):
            for define in json.load(open(path, encoding="utf-8")).get("versionDefines", []):
                self.assertNotRegex(define["expression"], r",\s*[)\]]", (path, define["expression"]))

    def test_prerelease_sorts_between_releases(self):
        keys = ["0.2.1", "0.2.1-beta.10", "0.2.1-beta.2", "0.2.1-beta.1", "0.2.0"]
        self.assertEqual(sorted(keys, key=vpm.version_key, reverse=True), keys)

    def test_entry_has_what_vpm_needs(self):
        data = make_zip("0.2.0")
        listing = vpm.build_listing([("https://example.test/kat-0.2.0.zip", data)], "https://example.test/index.json")
        entry = listing["packages"]["com.krislyz.kat"]["versions"]["0.2.0"]
        self.assertEqual(entry["url"], "https://example.test/kat-0.2.0.zip")
        self.assertEqual(entry["zipSHA256"], hashlib.sha256(data).hexdigest())
        self.assertEqual((entry["name"], entry["version"], entry["unity"]), ("com.krislyz.kat", "0.2.0", "2022.3"))
        self.assertEqual((listing["name"], listing["url"]), ("KAT Tools", "https://example.test/index.json"))
        self.assertTrue(listing["id"] and listing["author"])

    def test_versions_come_from_the_zip_and_are_listed_newest_first(self):
        listing = vpm.build_listing([("u/1", make_zip("0.9.0")), ("u/2", make_zip("0.10.0")), ("u/3", make_zip("0.2.1"))], "x")
        self.assertEqual(list(listing["packages"]["com.krislyz.kat"]["versions"]), ["0.10.0", "0.9.0", "0.2.1"])

    def test_the_real_package_json_is_valid_for_vpm(self):
        meta = json.load(open(os.path.join(PACKAGE_DIR, "package.json"), encoding="utf-8"))
        self.assertRegex(meta["name"], r"^[a-z0-9]+(\.[a-z0-9\-]+)+$")
        self.assertRegex(meta["version"], r"^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$")  # a pre-release like 0.2.1-beta.1 is allowed
        self.assertIn("displayName", meta)
        self.assertIn("unity", meta)
        self.assertNotIn("com.vrchat.avatars", meta.get("dependencies", {}), "the VRChat SDK stays optional")

    def test_landing_page_has_the_add_repo_link(self):
        page = vpm.landing_page("https://k.github.io/KAT-Tools/index.json")
        self.assertIn("vcc://vpm/addRepo?url=https://k.github.io/KAT-Tools/index.json", page)

    def test_a_server_can_write_the_listing_itself(self):
        import contextlib
        import tempfile
        data = make_zip("0.2.0")
        url = "https://cdn.example.test/vpm/index.json"
        import sys
        original, argv = vpm.release_zips, sys.argv
        with tempfile.TemporaryDirectory() as out:
            try:
                vpm.release_zips = lambda repository, token: [("https://example.test/a.zip", data)]
                sys.argv = ["m", "--repository", "Owner/Repo", "--url", url, "--index-only", "--out", out]
                with contextlib.redirect_stdout(io.StringIO()):
                    vpm.main()
                self.assertEqual(sorted(os.listdir(out)), ["index.json"], "only the listing, no page, no temp file")
                listing = json.load(open(os.path.join(out, "index.json"), encoding="utf-8"))
                self.assertEqual(listing["url"], url)
                first = open(os.path.join(out, "index.json"), "rb").read()
                vpm.release_zips = lambda repository, token: []  # GitHub answered with nothing: keep what is there
                with self.assertRaises(SystemExit):
                    vpm.main()
                self.assertEqual(open(os.path.join(out, "index.json"), "rb").read(), first)
                vpm.release_zips = lambda repository, token: 1 / 0  # GitHub did not answer at all
                with self.assertRaises(ZeroDivisionError):
                    vpm.main()
                self.assertEqual(open(os.path.join(out, "index.json"), "rb").read(), first)
            finally:
                vpm.release_zips, sys.argv = original, argv

    def test_blender_and_unity_versions_agree(self):
        manifest = open(os.path.join(HERE, "..", "kat", "blender_manifest.toml"), encoding="utf-8").read()
        blender = next(line.split('"')[1] for line in manifest.splitlines() if line.startswith("version ="))
        unity = json.load(open(os.path.join(PACKAGE_DIR, "package.json"), encoding="utf-8"))["version"]
        self.assertEqual(blender, unity, "a release tag has to match both")


if __name__ == "__main__":
    unittest.main(argv=["kat"], exit=False)
