import flet as ft

def normalize_key(raw_key: str) -> str:
    """Suaviza as diferenças de nomes de teclas entre Flet e o sistema operacional."""
    mapping = {
        "Arrow Up": "up",
        "Arrow Down": "down",
        "Arrow Left": "left",
        "Arrow Right": "right",
        "Control": "ctrl",
        "Shift": "shift",
        "Alt": "alt",
        "Meta": "windows",
        "Escape": "esc",
        "Enter": "enter",
        "Space": "space",
        "Backspace": "backspace",
        "Delete": "delete",
        "Tab": "tab",
    }
    key = mapping.get(raw_key, raw_key)
    return key.strip().lower()


def hotkey_from_event(e: ft.KeyboardEvent) -> str:
    """Converte um evento de teclado do Flet em uma string de atalho padronizada."""
    key = normalize_key(e.key)
    if key in {"", "ctrl", "shift", "alt", "windows", "meta"}:
        return ""

    if key == "esc" and not e.ctrl and not e.alt and not e.shift and not e.meta:
        return "esc"

    parts: list[str] = []
    # No Windows, Ctrl+Alt costuma ser interpretado como AltGr
    if e.ctrl and e.alt and not e.shift and not e.meta:
        parts.append("alt gr")
    else:
        if e.ctrl: parts.append("ctrl")
        if e.alt: parts.append("alt")
        if e.shift: parts.append("shift")
        if e.meta: parts.append("windows")

    parts.append(key)
    return "+".join(parts)


def expand_shortcut_variants(shortcut: str) -> list[str]:
    """Cria variações equivalentes para aumentar a compatibilidade de registro de atalhos."""
    raw = shortcut.strip()
    if not raw:
        return []

    variants = [raw]
    lowered = raw.lower()

    if "alt gr" in lowered:
        variants.append(lowered.replace("alt gr", "ctrl+alt"))
        variants.append(lowered.replace("alt gr", "right alt"))

    deduped: list[str] = []
    for variant in variants:
        normalized = "+".join(part.strip() for part in variant.split("+") if part.strip())
        if normalized and normalized not in deduped:
            deduped.append(normalized)

    return deduped
