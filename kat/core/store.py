# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Content-addressed snapshot store. No bpy in here, so it runs (and tests) anywhere."""
import hashlib
import json
import os
import shutil
import threading
import time

CHUNK = 1 << 20


def hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class Store:
    def __init__(self, root):
        self.root = root
        self.objects = os.path.join(root, "objects")
        self.index = os.path.join(root, "index.jsonl")
        self.lock = threading.RLock()
        os.makedirs(self.objects, exist_ok=True)

    def object_path(self, digest):
        return os.path.join(self.objects, digest[:2], digest)

    def _put(self, path):
        """Copy a file into the object folder. Returns its hash. Copy first, hash the copy: the file may still be changing."""
        part = os.path.join(self.objects, f"{threading.get_ident()}.part")
        shutil.copyfile(path, part)
        digest = hash_file(part)
        dest = self.object_path(digest)
        with self.lock:
            if os.path.exists(dest):
                os.remove(part)
            else:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                os.replace(part, dest)
        return digest

    @staticmethod
    def _relative(file, base, digest):
        """Where a texture goes next to the restored model: the same relative place when it lived under the model's
        folder, else a folder of its own so two files with one name cannot collide."""
        try:
            rel = os.path.relpath(file, base)
        except ValueError:  # another drive
            rel = os.pardir
        if rel.startswith(os.pardir) or os.path.isabs(rel):
            rel = os.path.join("_external", digest[:8], os.path.basename(file))
        return rel.replace("\\", "/")

    def add(self, path, op, files=(), **meta):
        """Store `path`. `files` are other files it needs (textures), kept alongside so a version can be rebuilt whole."""
        digest = self._put(path)
        extra = []
        base = os.path.dirname(os.path.abspath(path))
        for file in dict.fromkeys(files):  # once each, in order
            try:
                h = self._put(file)
                extra.append({"rel": self._relative(os.path.abspath(file), base, h), "hash": h,
                              "size": os.path.getsize(self.object_path(h))})
            except OSError:
                continue  # a texture that vanished or is locked must not lose the model itself
        dest = self.object_path(digest)
        with self.lock:
            now = time.time()
            entry = {
                "id": f"{int(now * 1000):x}-{digest[:8]}",
                "ts": now,
                "op": op,
                "src": os.path.abspath(path),
                "name": os.path.basename(path),
                "hash": digest,
                "size": os.path.getsize(dest),
                "files": extra,
                **meta,
            }
            with open(self.index, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        return entry

    def entries(self):
        out = []
        with self.lock:
            try:
                with open(self.index, encoding="utf-8") as f:
                    for line in f:
                        try:
                            out.append(json.loads(line))
                        except ValueError:
                            continue  # torn write from a crash, skip the line
            except FileNotFoundError:
                pass
            except OSError:
                pass  # briefly locked by antivirus or an indexer: show what we have, the next refresh will do better
        return out

    def get(self, entry_id):
        return next((e for e in self.entries() if e["id"] == entry_id), None)

    def restore(self, entry_id, dest):
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        shutil.copyfile(self.object_path(entry["hash"]), dest)
        return entry

    def restore_all(self, entry_id, folder):
        """Write the model and its textures into `folder`. Returns the model's path."""
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        root = os.path.abspath(folder)
        os.makedirs(root, exist_ok=True)
        for item in entry.get("files", ()):
            target = os.path.abspath(os.path.join(root, item["rel"]))
            if os.path.commonpath([root, target]) != root:  # the index is a plain file: never write outside the folder
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(self.object_path(item["hash"]), target)
        model = os.path.join(root, os.path.basename(entry["name"]))
        shutil.copyfile(self.object_path(entry["hash"]), model)
        return model

    def forget(self, entry_id):
        with self.lock:
            self._rewrite([e for e in self.entries() if e["id"] != entry_id])

    def prune(self, keep, max_bytes):
        """Keep the newest `keep` entries per (op, file name), then trim oldest until under max_bytes."""
        with self.lock:
            entries = sorted(self.entries(), key=lambda e: e["ts"], reverse=True)
            seen = {}
            kept = []
            for e in entries:
                group = (e["op"], e["name"])
                seen[group] = seen.get(group, 0) + 1
                if seen[group] <= keep:
                    kept.append(e)
            while kept and self._unique_size(kept) > max_bytes:
                kept.pop()
            self._rewrite(sorted(kept, key=lambda e: e["ts"]))

    @staticmethod
    def _blobs(entries):
        out = {}
        for e in entries:
            out[e["hash"]] = e["size"]
            for item in e.get("files", ()):
                out[item["hash"]] = item["size"]
        return out

    def _unique_size(self, entries):
        return sum(self._blobs(entries).values())

    def _rewrite(self, entries):
        tmp = self.index + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        for attempt in range(5):  # Windows refuses to replace a file someone else has open for a moment
            try:
                os.replace(tmp, self.index)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
        live = set(self._blobs(entries))
        for folder in os.listdir(self.objects):
            sub = os.path.join(self.objects, folder)
            if not os.path.isdir(sub):
                continue
            for digest in os.listdir(sub):
                if digest not in live:
                    os.remove(os.path.join(sub, digest))
