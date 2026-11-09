# SPDX-License-Identifier: GPL-3.0-or-later
"""Colour themes for the workspace, the viewport and the finale."""

_DARK_CURSORS = ["ff8a3d", "4f8cff", "3ddcc0", "ff5a7a", "ffd166", "b48cff", "7be04f", "ff74c8",
                 "5fd7ff", "ffa94d", "9aa8ff", "6ef2a8"]

THEMES = {
    "graphite": dict(
        bg="0c0d10", panel="17191e", head="20232a", row_alt="1c1e24", text="cdd1d9", dim="7c828e", faint="4a4f5a",
        field="2a2e36", field_hi="353a44", accent="ff8a3d", blue="4f8cff", teal="3ddcc0", rose="ff5a7a", gold="ffd166",
        select="3a5ea8", select_text="ffffff", line="2b2e36", shelf="262931", icon="a7adb9", strip="1e2026", modrow="252830",
        logalt="93a0b5", hist="2e3a4f", node="292c33", dots="262a32", chip="0c0e13", chip_text="ffffff", key="ffffff",
        sheet="0b0c0f", cursors=_DARK_CURSORS,
        clay=(118, 121, 129), wire=(12, 13, 16), sel=(255, 138, 61), front=(120, 214, 255),
        view_base=(0.0115, 0.0130, 0.0170), view_glow=(0.0225, 0.0255, 0.0340), grid=(0.060, 0.064, 0.074),
        backdrop_bottom=(0.040, 0.046, 0.060), backdrop_top=(0.009, 0.011, 0.016), floor=(0.050, 0.055, 0.070)),
    "midnight": dict(
        bg="070b14", panel="0f1625", head="172138", row_alt="131b2e", text="c9d6ea", dim="7287a8", faint="3f4f6b",
        field="1d2a44", field_hi="263759", accent="52d6ff", blue="3f7bff", teal="5ff0c8", rose="ff6a9a", gold="ffd479",
        select="2c55b5", select_text="ffffff", line="222f4a", shelf="18233a", icon="a3b6d4", strip="121b30", modrow="1a2640",
        logalt="8aa0c2", hist="23324f", node="1c2840", dots="1f2c46", chip="0a0f1c", chip_text="ffffff", key="ffffff",
        sheet="060a12", cursors=["52d6ff", "3f7bff", "5ff0c8", "9db7ff", "ffd479", "ff6a9a", "7fe7ff", "b9a4ff"],
        clay=(112, 128, 156), wire=(8, 12, 22), sel=(82, 214, 255), front=(255, 160, 120),
        view_base=(0.0070, 0.0100, 0.0200), view_glow=(0.0120, 0.0240, 0.0600), grid=(0.035, 0.060, 0.110),
        backdrop_bottom=(0.016, 0.030, 0.075), backdrop_top=(0.003, 0.006, 0.018), floor=(0.022, 0.034, 0.075)),
    "paper": dict(
        bg="e9e6df", panel="f6f3ec", head="ebe7df", row_alt="efebe3", text="2a2d33", dim="77736b", faint="b2ada2",
        field="e2ddd2", field_hi="d8d2c5", accent="e4572e", blue="2f6fde", teal="12a58a", rose="d63f6b", gold="c98a12",
        select="b9cdf5", select_text="1b2a4a", line="d8d3c8", shelf="efebe3", icon="6f6b64", strip="ebe6dc", modrow="e7e2d7",
        logalt="8a867e", hist="d3cfc4", node="fbf9f4", dots="d7d2c6", chip="fbf9f4", chip_text="2a2d33", key="2a2d33",
        sheet="f2efe8", cursors=["e4572e", "2f6fde", "12a58a", "d63f6b", "c98a12", "7c54d6", "3f9a2c", "c2389e"],
        clay=(198, 194, 186), wire=(70, 67, 62), sel=(228, 87, 46), front=(47, 111, 222),
        view_base=(0.56, 0.54, 0.50), view_glow=(0.26, 0.25, 0.23), grid=(-0.22, -0.22, -0.22),
        backdrop_bottom=(0.62, 0.60, 0.56), backdrop_top=(0.86, 0.84, 0.80), floor=(0.70, 0.68, 0.63)),
    "neon": dict(
        bg="08060f", panel="120e1f", head="1b1530", row_alt="161127", text="e3d9ff", dim="8a7bb0", faint="4d4270",
        field="251c40", field_hi="30255a", accent="ff2fd0", blue="7a5cff", teal="20f0ff", rose="ff4f8f", gold="ffe14f",
        select="5a2fc0", select_text="ffffff", line="2a2148", shelf="1d1635", icon="b8a8e0", strip="17112a", modrow="211838",
        logalt="a08fd0", hist="2b2150", node="1f1836", dots="2a2048", chip="0a0714", chip_text="ffffff", key="ffffff",
        sheet="07050c", cursors=["ff2fd0", "20f0ff", "7a5cff", "ffe14f", "ff4f8f", "5cffa1"],
        clay=(96, 84, 140), wire=(14, 8, 30), sel=(255, 47, 208), front=(32, 240, 255),
        view_base=(0.0040, 0.0030, 0.0140), view_glow=(0.0200, 0.0080, 0.0600), grid=(0.100, 0.020, 0.160),
        backdrop_bottom=(0.030, 0.006, 0.070), backdrop_top=(0.004, 0.002, 0.012), floor=(0.020, 0.010, 0.050)),
    "terminal": dict(
        bg="030604", panel="07100a", head="0c1a10", row_alt="091510", text="9dffb0", dim="4fa765", faint="2b5c39",
        field="10261a", field_hi="173423", accent="39ff77", blue="22c55e", teal="7dffb0", rose="ffdd55", gold="d4ff55",
        select="14582e", select_text="d8ffe0", line="123020", shelf="0e1f15", icon="6cdd88", strip="0a1810", modrow="102417",
        logalt="5fbf7a", hist="13341f", node="0e2016", dots="12301e", chip="020503", chip_text="b6ffc4", key="c8ffd4",
        sheet="020403", cursors=["39ff77", "7dffb0", "22c55e", "d4ff55", "ffdd55"],
        clay=(96, 130, 104), wire=(4, 10, 6), sel=(57, 255, 119), front=(212, 255, 85),
        view_base=(0.0020, 0.0060, 0.0030), view_glow=(0.0040, 0.0220, 0.0100), grid=(0.010, 0.070, 0.030),
        backdrop_bottom=(0.006, 0.030, 0.012), backdrop_top=(0.001, 0.004, 0.002), floor=(0.008, 0.030, 0.014)),
    "blueprint": dict(
        bg="0b2a55", panel="0e3364", head="123d75", row_alt="103869", text="e6f0ff", dim="9fbbe0", faint="5d83b5",
        field="17477f", field_hi="1d5492", accent="ffd166", blue="6fb4ff", teal="8fe8ff", rose="ff9db0", gold="ffd166",
        select="2a66ad", select_text="ffffff", line="1a4a85", shelf="12407a", icon="c4d8f2", strip="0f376a", modrow="154379",
        logalt="b3cbea", hist="1a4e8a", node="12407a", dots="1a4e8a", chip="082242", chip_text="ffffff", key="ffffff",
        sheet="0a2850", cursors=["ffffff", "ffd166", "8fe8ff", "ff9db0", "c4d8f2"],
        clay=(150, 185, 225), wire=(12, 36, 76), sel=(255, 209, 102), front=(255, 255, 255),
        view_base=(0.0120, 0.0500, 0.1600), view_glow=(0.0200, 0.0500, 0.1200), grid=(0.100, 0.160, 0.240),
        backdrop_bottom=(0.016, 0.070, 0.210), backdrop_top=(0.006, 0.030, 0.110), floor=(0.022, 0.085, 0.230)),
}

# name: (key, fill, rim, top) as (power multiplier, colour), then world colour and strength
RIGS = {
    "studio": dict(key=(1.0, (1.0, 0.93, 0.84)), fill=(1.0, (0.66, 0.78, 1.0)), rim=(1.0, (1.0, 0.72, 0.5)), top=(1.0, (0.95, 0.97, 1.0)),
                   world=(0.21, 0.25, 0.34), strength=0.62),
    "sunset": dict(key=(1.25, (1.0, 0.58, 0.28)), fill=(0.55, (0.55, 0.42, 1.0)), rim=(1.3, (1.0, 0.36, 0.48)), top=(0.35, (1.0, 0.8, 0.7)),
                   world=(0.34, 0.2, 0.28), strength=0.5),
    "noir": dict(key=(1.5, (1.0, 1.0, 1.0)), fill=(0.08, (1.0, 1.0, 1.0)), rim=(1.3, (1.0, 1.0, 1.0)), top=(0.12, (1.0, 1.0, 1.0)),
                 world=(0.1, 0.1, 0.1), strength=0.3),
    "clean": dict(key=(0.8, (1.0, 0.98, 0.95)), fill=(1.2, (0.92, 0.96, 1.0)), rim=(0.5, (1.0, 1.0, 1.0)), top=(1.4, (1.0, 1.0, 1.0)),
                  world=(0.62, 0.64, 0.68), strength=1.0),
    "neon": dict(key=(1.1, (1.0, 0.16, 0.78)), fill=(1.6, (0.1, 0.9, 1.0)), rim=(1.6, (0.5, 0.3, 1.0)), top=(0.3, (0.7, 0.5, 1.0)),
                 world=(0.1, 0.06, 0.2), strength=0.5),
}


def theme(name):
    return THEMES.get(name, THEMES["graphite"])


def srgb_to_linear(hex_str):
    v = hex_str.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(v[i:i + 2], 16) / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return tuple(out)
