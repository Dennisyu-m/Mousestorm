# SPDX-License-Identifier: GPL-3.0-or-later
# Runs INSIDE Blender (this file uses the bpy API, hence GPL).
#
# Lists the GPUs Cycles can render on, in the backend order beauty.py uses.
import json

import bpy

prefs = bpy.context.preferences.addons["cycles"].preferences
found = []
for backend in ("OPTIX", "CUDA", "HRT", "ONEAPI", "METAL"):
    try:
        prefs.compute_device_type = backend
        prefs.get_devices()
        found = [dict(id=d.id, name=d.name, backend=backend) for d in prefs.devices if d.type == backend]
    except Exception:
        found = []
    if found:
        break
print("MOUSESTORM_DEVICES " + json.dumps(found))
