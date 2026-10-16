# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Pure-Python store tests. Run: blender -b --factory-startup --python tests/test_store.py"""
import importlib.util
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("kat_store", os.path.join(HERE, "..", "kat", "core", "store.py"))
store_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store_mod)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = store_mod.Store(os.path.join(self.tmp.name, "vault"))

    def make(self, name, data):
        path = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_add_and_restore_roundtrip(self):
        path = self.make("a.fbx", b"model-v1")
        entry = self.store.add(path, "export")
        dest = os.path.join(self.tmp.name, "back.fbx")
        self.store.restore(entry["id"], dest)
        self.assertEqual(open(dest, "rb").read(), b"model-v1")

    def test_textures_travel_with_the_model(self):
        model = self.make("sub/a.fbx", b"model")
        inside = self.make("sub/tex/skin.png", b"png-1")
        outside = self.make("elsewhere/skin.png", b"png-2")  # same name, other folder
        entry = self.store.add(model, "import", files=[inside, outside, os.path.join(self.tmp.name, "gone.png")])
        self.assertEqual(sorted(f["rel"].split("/")[0] for f in entry["files"]), ["_external", "tex"])
        folder = os.path.join(self.tmp.name, "back")
        path = self.store.restore_all(entry["id"], folder)
        self.assertEqual(open(path, "rb").read(), b"model")
        self.assertEqual(open(os.path.join(folder, "tex", "skin.png"), "rb").read(), b"png-1")
        external = next(f for f in entry["files"] if f["rel"].startswith("_external"))
        self.assertEqual(open(os.path.join(folder, *external["rel"].split("/")), "rb").read(), b"png-2")

    def test_forgetting_and_pruning_count_textures(self):
        model = self.make("a.fbx", b"m")
        tex = self.make("t.png", b"x" * 100)
        entry = self.store.add(model, "import", files=[tex])
        self.store.prune(10, 50)  # the texture alone is over the limit, so the entry goes
        self.assertEqual(self.store.entries(), [])
        live = [f for _, _, fs in os.walk(self.store.objects) for f in fs]
        self.assertEqual(live, [])
        self.assertIsNone(entry and self.store.get(entry["id"]))

    def test_a_poisoned_index_cannot_write_outside_the_folder(self):
        model = self.make("a.fbx", b"m")
        entry = self.store.add(model, "import", files=[self.make("t.png", b"x")])
        entry["files"][0]["rel"] = "../../escape.png"
        self.store._rewrite([entry])
        folder = os.path.join(self.tmp.name, "back")
        self.store.restore_all(entry["id"], folder)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "escape.png")))

    def test_identical_content_is_stored_once(self):
        path = self.make("a.fbx", b"same")
        self.store.add(path, "export")
        self.store.add(path, "export")
        self.assertEqual(len(self.store.entries()), 2)
        objects = [f for _, _, fs in os.walk(self.store.objects) for f in fs]
        self.assertEqual(len(objects), 1)

    def test_changed_file_keeps_old_version(self):
        path = self.make("a.fbx", b"one")
        first = self.store.add(path, "export")
        self.make("a.fbx", b"two")
        self.store.add(path, "export")
        dest = os.path.join(self.tmp.name, "old.fbx")
        self.store.restore(first["id"], dest)
        self.assertEqual(open(dest, "rb").read(), b"one")

    def test_prune_keeps_newest_per_file(self):
        path = self.make("a.fbx", b"0")
        for i in range(5):
            self.make("a.fbx", str(i).encode())
            self.store.add(path, "export")
        self.store.prune(keep=2, max_bytes=1 << 30)
        kept = self.store.entries()
        self.assertEqual(len(kept), 2)
        self.assertEqual([open(self.store.object_path(e["hash"]), "rb").read() for e in kept], [b"3", b"4"])

    def test_prune_trims_oldest_to_size_cap(self):
        path = self.make("a.fbx", b"x")
        for i in range(4):
            self.make("a.fbx", bytes([65 + i]) * 100)
            self.store.add(path, "export")
        self.store.prune(keep=10, max_bytes=250)
        self.assertEqual(len(self.store.entries()), 2)

    def test_prune_deletes_orphan_objects(self):
        path = self.make("a.fbx", b"one")
        self.store.add(path, "export")
        self.make("a.fbx", b"two")
        self.store.add(path, "export")
        self.store.prune(keep=1, max_bytes=1 << 30)
        objects = [f for _, _, fs in os.walk(self.store.objects) for f in fs]
        self.assertEqual(len(objects), 1)

    def test_forget_removes_entry(self):
        entry = self.store.add(self.make("a.fbx", b"z"), "import")
        self.store.forget(entry["id"])
        self.assertIsNone(self.store.get(entry["id"]))

    def test_torn_index_line_is_skipped(self):
        self.store.add(self.make("a.fbx", b"z"), "import")
        with open(self.store.index, "a", encoding="utf-8") as f:
            f.write('{"id": "broken')
        self.assertEqual(len(self.store.entries()), 1)


if __name__ == "__main__":
    unittest.main(argv=["kat"], exit=False)
