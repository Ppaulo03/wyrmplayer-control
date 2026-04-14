import flet as ft
from src.core.config import ConfigManager, AppConfig


def main(page: ft.Page) -> None:
    page.title = "Configurações - Music HUD"
    page.window.width = 540
    page.window.height = 720
    page.window.min_width = 460
    page.window.min_height = 620
    page.window.resizable = True
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#070B12"
    page.padding = 18
    page.scroll = ft.ScrollMode.AUTO

    config_manager = ConfigManager()
    cfg = config_manager.load()

    # --- Controles de volume e HUD ---
    volume_step = ft.Slider(
        min=1, max=20, divisions=19, label="{value}%", value=cfg.volume_step
    )
    hud_time = ft.Slider(
        min=1, max=10, divisions=9, label="{value}s", value=cfg.hud_display_time
    )

    # --- Hotkeys (captura) ---
    field_style = ft.InputBorder.OUTLINE
    hk_play = ft.TextField(
        label="Play/Pause",
        value=cfg.hotkeys["play_pause"],
        border=field_style,
        dense=True,
        read_only=True,
        expand=True,
    )
    hk_next = ft.TextField(
        label="Próxima",
        value=cfg.hotkeys["next_track"],
        border=field_style,
        dense=True,
        read_only=True,
        expand=True,
    )
    hk_prev = ft.TextField(
        label="Anterior",
        value=cfg.hotkeys["previous_track"],
        border=field_style,
        dense=True,
        read_only=True,
        expand=True,
    )
    hk_up = ft.TextField(
        label="Volume +",
        value=cfg.hotkeys["volume_up"],
        border=field_style,
        dense=True,
        read_only=True,
        expand=True,
    )
    hk_down = ft.TextField(
        label="Volume -",
        value=cfg.hotkeys["volume_down"],
        border=field_style,
        dense=True,
        read_only=True,
        expand=True,
    )
    hk_mute = ft.TextField(
        label="Mute",
        value=cfg.hotkeys["mute"],
        border=field_style,
        dense=True,
        read_only=True,
        expand=True,
    )

    # --- Gatilhos (Triggers) ---
    t_vol = ft.Switch(label="Mudar Volume", value=cfg.triggers["volume"])
    t_meta = ft.Switch(label="Mudar Música", value=cfg.triggers["metadata"])
    t_play = ft.Switch(label="Pausar/Play", value=cfg.triggers["playback"])

    autosave_status = ft.Text("Salvamento automático ativo", size=12, color=ft.Colors.GREEN_300)

    capture_state: dict[str, object] = {"field": None, "label": ""}

    def _normalize_key(raw_key: str) -> str:
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

    def _hotkey_from_event(e: ft.KeyboardEvent) -> str:
        key = _normalize_key(e.key)
        if key in {"", "ctrl", "shift", "alt", "windows", "meta"}:
            return ""

        if key == "esc" and not e.ctrl and not e.alt and not e.shift and not e.meta:
            return "esc"

        parts: list[str] = []
        # Mantem compatibilidade visual com config padrao no Windows.
        if e.ctrl and e.alt and not e.shift and not e.meta:
            parts.append("alt gr")
        else:
            if e.ctrl:
                parts.append("ctrl")
            if e.alt:
                parts.append("alt")
            if e.shift:
                parts.append("shift")
            if e.meta:
                parts.append("windows")

        parts.append(key)
        return "+".join(parts)

    def save_settings() -> None:
        new_cfg = AppConfig(
            volume_step=int(volume_step.value),
            hud_display_time=int(hud_time.value),
            hotkeys={
                "play_pause": (hk_play.value or "").strip(),
                "next_track": (hk_next.value or "").strip(),
                "previous_track": (hk_prev.value or "").strip(),
                "volume_up": (hk_up.value or "").strip(),
                "volume_down": (hk_down.value or "").strip(),
                "mute": (hk_mute.value or "").strip(),
            },
            triggers={
                "volume": t_vol.value,
                "metadata": t_meta.value,
                "playback": t_play.value,
            },
        )
        config_manager.save(new_cfg)

    def on_live_change(e: ft.ControlEvent) -> None:
        save_settings()
        page.update()

    def stop_capture() -> None:
        capture_state["field"] = None
        capture_state["label"] = ""
        page.on_keyboard_event = None

    def on_capture_key(e: ft.KeyboardEvent) -> None:
        field = capture_state.get("field")
        label = str(capture_state.get("label") or "")
        if not isinstance(field, ft.TextField):
            return

        hotkey = _hotkey_from_event(e)
        if not hotkey:
            return

        if hotkey == "esc":
            autosave_status.value = "Captura cancelada"
            autosave_status.color = ft.Colors.ORANGE_300
            stop_capture()
            page.update()
            return

        field.value = hotkey
        save_settings()
        autosave_status.value = f"Atalho de {label} salvo: {hotkey}"
        autosave_status.color = ft.Colors.GREEN_300
        stop_capture()
        page.update()

    def start_capture(field: ft.TextField, label: str) -> None:
        capture_state["field"] = field
        capture_state["label"] = label
        autosave_status.value = f"Pressione o novo atalho para {label} (Esc cancela)"
        autosave_status.color = ft.Colors.AMBER_300
        page.on_keyboard_event = on_capture_key
        page.update()

    def hotkey_row(label: str, field: ft.TextField) -> ft.Control:
        return ft.Row(
            [
                field,
                ft.OutlinedButton(
                    "Gravar",
                    icon=ft.Icons.KEYBOARD,
                    on_click=lambda _: start_capture(field, label),
                ),
                ft.IconButton(
                    ft.Icons.CLOSE,
                    tooltip="Limpar atalho",
                    on_click=lambda _: (
                        setattr(field, "value", ""),
                        save_settings(),
                        setattr(autosave_status, "value", f"Atalho de {label} limpo"),
                        setattr(autosave_status, "color", ft.Colors.ORANGE_300),
                        page.update(),
                    ),
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # Sliders e switches salvam imediatamente.
    volume_step.on_change = on_live_change
    hud_time.on_change = on_live_change
    t_vol.on_change = on_live_change
    t_meta.on_change = on_live_change
    t_play.on_change = on_live_change

    section_style = {
        "padding": 16,
        "border_radius": 14,
        "bgcolor": "#0D1422",
        "border": ft.Border.all(1, "#23314A"),
    }

    general_card = ft.Container(
        content=ft.Column(
            [
                ft.Text("Ajustes Gerais", size=24, weight=ft.FontWeight.BOLD),
                ft.Text("Passo do volume (%)", size=13, color=ft.Colors.WHITE70),
                volume_step,
                ft.Text("Tempo do HUD (segundos)", size=13, color=ft.Colors.WHITE70),
                hud_time,
            ],
            spacing=10,
            tight=True,
        ),
        **section_style,
    )

    hotkeys_card = ft.Container(
        content=ft.Column(
            [
                ft.Text("Atalhos de Teclado", size=24, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "Clique em Gravar e pressione a combinação. O salvamento é automático.",
                    size=12,
                    color=ft.Colors.WHITE60,
                ),
                hotkey_row("Play/Pause", hk_play),
                hotkey_row("Anterior", hk_prev),
                hotkey_row("Próxima", hk_next),
                hotkey_row("Volume +", hk_up),
                hotkey_row("Volume -", hk_down),
                hotkey_row("Mute", hk_mute),
            ],
            spacing=10,
            tight=True,
        ),
        **section_style,
    )

    triggers_card = ft.Container(
        content=ft.Column(
            [
                ft.Text("Quando Mostrar o HUD", size=24, weight=ft.FontWeight.BOLD),
                ft.Container(t_vol, bgcolor="#101A2C", border_radius=10, padding=10),
                ft.Container(t_meta, bgcolor="#101A2C", border_radius=10, padding=10),
                ft.Container(t_play, bgcolor="#101A2C", border_radius=10, padding=10),
            ],
            spacing=10,
            tight=True,
        ),
        **section_style,
    )

    page.add(
        ft.Column(
            [
                general_card,
                hotkeys_card,
                triggers_card,
                ft.Row(
                    [
                        ft.Icon(ft.Icons.CLOUD_DONE, size=16, color=ft.Colors.GREEN_300),
                        autosave_status,
                    ],
                    spacing=8,
                ),
            ],
            spacing=14,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )
    )


if __name__ == "__main__":
    ft.run(main=main)
