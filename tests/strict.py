# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Import first in every Blender test: calling an API Blender plans to remove becomes an error."""
import warnings

warnings.filterwarnings("error", message=r".*removed in Blender.*", category=DeprecationWarning)
