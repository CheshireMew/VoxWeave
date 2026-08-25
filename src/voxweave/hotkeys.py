from __future__ import annotations

MODIFIERS = {
    "alt": 0x0001,
    "ctrl": 0x0002,
    "control": 0x0002,
    "shift": 0x0004,
    "win": 0x0008,
    "meta": 0x0008,
}


def parse_windows_hotkey(value: str) -> tuple[int, int]:
    parts = [part.strip().casefold() for part in value.split("+") if part.strip()]
    if len(parts) < 2:
        raise ValueError(f"invalid global hotkey: {value}")
    modifiers = 0x4000  # MOD_NOREPEAT
    seen: set[int] = set()
    for part in parts[:-1]:
        if part not in MODIFIERS:
            raise ValueError(f"unsupported hotkey modifier: {part}")
        modifier = MODIFIERS[part]
        if modifier in seen:
            raise ValueError(f"duplicate hotkey modifier: {part}")
        seen.add(modifier)
        modifiers |= modifier
    key = parts[-1]
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        virtual_key = 0x70 + int(key[1:]) - 1
    elif len(key) == 1 and key.upper() in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        virtual_key = ord(key.upper())
    else:
        raise ValueError(f"unsupported hotkey key: {key}")
    return modifiers, virtual_key
