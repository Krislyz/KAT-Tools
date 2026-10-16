# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Run every test in a real Blender.
  python tests/run_all.py [path to blender]      (uses $KAT_BLENDER, then `blender` on PATH)
  blender -b --python tests/run_all.py           (uses that Blender)"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import bpy
    blender = bpy.app.binary_path
except ImportError:
    blender = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KAT_BLENDER", "blender")


def run_test(name):
    run = subprocess.run([blender, "-b", "--factory-startup", "--python-exit-code", "1",
                          "--python", os.path.join(HERE, name)], capture_output=True, text=True)
    text = run.stdout + run.stderr
    return run, text, run.returncode == 0 and (" OK" in text or "\nOK" in text)


failed = []
for name in sorted(os.listdir(HERE)):
    if name.startswith(("test_", "blender_")) and name.endswith(".py"):
        run, text, ok = run_test(name)
        if not ok and "blender.crash.txt" in text:
            # Blender itself died (a segfault, no Python error): show where, then give it one more go
            try:
                with open(os.path.join(tempfile.gettempdir(), "blender.crash.txt"), encoding="utf-8", errors="replace") as crash:
                    print(f"CRASH {name}, Blender's stack:\n" + crash.read()[-3000:], flush=True)
            except OSError:
                print(f"CRASH {name}, no crash file", flush=True)
            run, text, ok = run_test(name)
        print(("PASS " if ok else "FAIL ") + name, flush=True)
        if not ok:
            failed.append(name)
            print(run.stdout[-1500:], run.stderr[-500:])
sys.exit(1 if failed else 0)
