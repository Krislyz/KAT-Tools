# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Small drawing helpers the panels share, so every panel looks and reads the same."""
import textwrap

FOLDED = "TRIA_RIGHT"
OPEN = "TRIA_DOWN"


def chars_per_line(context, margin=56):
    """How many characters fit on a label in this sidebar. Labels do not wrap by themselves."""
    region = context.region
    if region is None:
        return 40
    scale = max(0.5, context.preferences.system.ui_scale)
    return max(16, int((region.width - margin) / (6.6 * scale)))


def paragraph(layout, context, text, icon="NONE"):
    """Wrapped text. The first line carries the icon, the rest line up under it."""
    col = layout.column(align=True)
    col.scale_y = 0.8
    for i, line in enumerate(textwrap.wrap(text, chars_per_line(context)) or [""]):
        col.label(text=line, icon=icon if i == 0 else ("BLANK1" if icon != "NONE" else "NONE"))
    return col


def status(layout, ok, text, bad_text=None):
    """One line: a tick when fine, a red cross-mark when something is needed."""
    row = layout.row()
    row.alert = not ok
    row.label(text=text if ok or bad_text is None else bad_text, icon="CHECKMARK" if ok else "ERROR")
    return row


def foldout(layout, owner, prop, text):
    """A small collapsible section inside a panel. Returns True while it is open."""
    is_open = getattr(owner, prop)
    layout.prop(owner, prop, text=text, icon=OPEN if is_open else FOLDED, emboss=False)
    return is_open


def bar(layout, factor, text):
    layout.progress(factor=min(1.0, max(0.0, factor)), type="BAR", text=text)
