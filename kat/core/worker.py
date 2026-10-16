# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""One background thread for all file work. Never touch bpy from jobs submitted here."""
import logging
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("kat")
_pool = None


def _report(future):
    err = future.exception()
    if err:
        log.error("kat: background job failed: %r", err)


def submit(fn, *args, **kwargs):
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kat")
    future = _pool.submit(fn, *args, **kwargs)
    future.add_done_callback(_report)
    return future


def shutdown():
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None
