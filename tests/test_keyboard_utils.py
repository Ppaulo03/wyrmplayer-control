import flet as ft
from src.core.utils import keyboard as kb

def test_normalize_key_arrows():
    assert kb.normalize_key("Arrow Up") == "up"
    assert kb.normalize_key("Arrow Down") == "down"

def test_normalize_key_mods():
    assert kb.normalize_key("Control") == "ctrl"
    assert kb.normalize_key("Shift") == "shift"

def test_expand_variants_alt_gr():
    variants = kb.expand_shortcut_variants("alt gr+p")
    assert "alt gr+p" in variants
    assert "ctrl+alt+p" in variants
    assert "right alt+p" in variants

def test_expand_variants_simple():
    variants = kb.expand_shortcut_variants("ctrl+s")
    assert variants == ["ctrl+s"]

class MockEvent:
    def __init__(self, key, ctrl=False, alt=False, shift=False, meta=False):
        self.key = key
        self.ctrl = ctrl
        self.alt = alt
        self.shift = shift
        self.meta = meta

def test_hotkey_from_event_combo():
    e = MockEvent("P", ctrl=True, alt=True)
    assert kb.hotkey_from_event(e) == "alt gr+p"
    
    e2 = MockEvent("N", ctrl=True)
    assert kb.hotkey_from_event(e2) == "ctrl+n"
