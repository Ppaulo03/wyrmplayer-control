import flet as ft
from src.core.config import AppConfig
from src.core.utils.keyboard import hotkey_from_event

def hotkeys_tab(cfg: AppConfig, on_save: callable, status_text: ft.Text) -> ft.Control:
    """Aba de Atalhos de Teclado com lógica de captura integrada."""
    
    fields = {}
    capturing = {"field": None, "label": ""}

    def stop_capture(page: ft.Page):
        capturing["field"] = None
        capturing["label"] = ""
        page.on_keyboard_event = None
        page.update()

    def on_capture_key(e: ft.KeyboardEvent):
        field = capturing["field"]
        label = capturing["label"]
        if not field: return

        hotkey = hotkey_from_event(e)
        if not hotkey: return

        if hotkey == "esc":
            status_text.value = "Captura cancelada"
            status_text.color = ft.Colors.ORANGE_300
            stop_capture(e.page)
            return

        field.value = hotkey
        on_save()
        status_text.value = f"Atalho de {label} salvo: {hotkey}"
        status_text.color = ft.Colors.GREEN_300
        stop_capture(e.page)

    def start_capture(field: ft.TextField, label: str, page: ft.Page):
        capturing["field"] = field
        capturing["label"] = label
        status_text.value = f"Pressione o novo atalho para {label} (Esc cancela)"
        status_text.color = ft.Colors.AMBER_300
        page.on_keyboard_event = on_capture_key
        page.update()

    def on_clear(label: str, field: ft.TextField, page: ft.Page):
        field.value = ""
        on_save()
        status_text.value = f"Atalho de {label} limpo"
        status_text.color = ft.Colors.ORANGE_300
        page.update()

    def hotkey_row(label: str, key: str) -> ft.Control:
        field = ft.TextField(
            label=label, value=cfg.hotkeys.get(key, ""),
            border=ft.InputBorder.OUTLINE, dense=True, read_only=True, expand=True
        )
        fields[key] = field
        return ft.Row([
            field,
            ft.OutlinedButton("Gravar", icon=ft.Icons.KEYBOARD, on_click=lambda e: start_capture(field, label, e.page)),
            ft.IconButton(ft.Icons.CLOSE, tooltip="Limpar", on_click=lambda e: on_clear(label, field, e.page))
        ], spacing=8)

    card = ft.Container(
        padding=16, border_radius=14, bgcolor="#0D1422", border=ft.border.all(1, "#23314A"),
        content=ft.Column([
            ft.Text("Atalhos de Teclado", size=24, weight=ft.FontWeight.BOLD),
            ft.Text("Clique em Gravar para capturar. Salvamento automático.", size=12, color=ft.Colors.WHITE60),
            hotkey_row("Play/Pause", "play_pause"),
            hotkey_row("Anterior", "previous_track"),
            hotkey_row("Próxima", "next_track"),
            hotkey_row("Volume +", "volume_up"),
            hotkey_row("Volume -", "volume_down"),
            hotkey_row("Mute", "mute"),
        ], spacing=10, tight=True)
    )
    card.data = fields
    return card
