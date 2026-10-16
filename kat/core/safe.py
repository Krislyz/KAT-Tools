# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Never let a QoL feature crash Blender: wrap entry points, log, move on."""
import functools
import logging

log = logging.getLogger("kat")


def guarded(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception:
            log.exception("kat: %s failed", fn.__name__)
            return None
    return wrapper


def invoke_guard(fn):
    """The same for Operator.invoke, which also gets the event."""
    @functools.wraps(fn)
    def wrapper(self, context, event):
        import bpy
        try:
            return fn(self, context, event)
        except ValueError as err:
            self.report({"ERROR"}, str(err))
        except (RuntimeError, TypeError, KeyError, AttributeError, OSError) as err:
            log.exception("kat: %s failed", self.bl_idname)
            self.report({"ERROR"}, f"{self.bl_label} failed: {err} (details in the system console)")
        return {"CANCELLED"}
    return wrapper


def operator_guard(fn):
    """For Operator.execute: leave Edit mode first, and turn failures into a message, not a traceback.
    ValueError is our own "that can't be done, here is why" and is shown as written."""
    @functools.wraps(fn)
    def wrapper(self, context):
        import bpy
        try:
            active = context.active_object
            if active is not None and active.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            return fn(self, context)
        except ValueError as err:
            self.report({"ERROR"}, str(err))
        except (RuntimeError, TypeError, KeyError, AttributeError, OSError) as err:
            log.exception("kat: %s failed", self.bl_idname)
            self.report({"ERROR"}, f"{self.bl_label} failed: {err} (details in the system console)")
        return {"CANCELLED"}
    return wrapper
