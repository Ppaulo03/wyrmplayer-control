import os
import sys
from pathlib import Path

import flet as ft
from src.core.display import HUD_POSITION_PRESETS, list_monitors
from src.core.config import ConfigManager, AppConfig


def main(page: ft.Page) -> None:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_dir = Path(getattr(sys, "_MEIPASS"))
    else:
        base_dir = Path(__file__).resolve().parents[2]

    settings_icon = str(base_dir / os.path.join("assets", "icon.ico"))

    page.title = "Configurações - Music HUD"
    page.window.icon = settings_icon
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
    log_level = ft.Dropdown(
        label="Nível de log",
        value=(cfg.log_level or "INFO").upper(),
        options=[
            ft.DropdownOption(key="DEBUG", text="DEBUG"),
            ft.DropdownOption(key="INFO", text="INFO"),
            ft.DropdownOption(key="WARNING", text="WARNING"),
            ft.DropdownOption(key="ERROR", text="ERROR"),
            ft.DropdownOption(key="CRITICAL", text="CRITICAL"),
        ],
        expand=True,
    )
    log_file = ft.TextField(
        label="Arquivo de log",
        value=cfg.log_file,
        hint_text="wyrmplayer.log",
        dense=True,
        expand=True,
    )
    websocket_port = ft.TextField(
        label="Porta do WebSocket",
        value=str(cfg.websocket_port),
        hint_text="8975",
        dense=True,
        expand=True,
        keyboard_type=ft.KeyboardType.NUMBER,
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

    monitors = list_monitors()
    monitor_dropdown = ft.Dropdown(
        label="Tela do overlay",
        value=str(min(max(cfg.hud_monitor, 0), len(monitors) - 1)),
        options=[
            ft.DropdownOption(key=str(monitor.index), text=monitor.label)
            for monitor in monitors
        ],
        expand=True,
    )

    position_dropdown = ft.Dropdown(
        label="Posição do overlay",
        value=(
            cfg.hud_position
            if cfg.hud_position in HUD_POSITION_PRESETS
            else "bottom_right"
        ),
        options=[
            ft.DropdownOption(key=key, text=label)
            for key, label in HUD_POSITION_PRESETS.items()
        ],
        expand=True,
    )

    autosave_status = ft.Text(
        "Salvamento automático ativo", size=12, color=ft.Colors.GREEN_300
    )

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
        raw_port = str(websocket_port.value or "").strip()
        try:
            port_value = int(raw_port)
        except ValueError:
            port_value = cfg.websocket_port

        if port_value < 1 or port_value > 65535:
            port_value = cfg.websocket_port

        new_cfg = AppConfig(
            volume_step=int(volume_step.value),
            hud_display_time=int(hud_time.value),
            websocket_port=port_value,
            hud_monitor=int(monitor_dropdown.value or 0),
            hud_position=position_dropdown.value or "bottom_right",
            log_level=str(log_level.value or "INFO").upper(),
            log_file=(log_file.value or "wyrmplayer.log").strip(),
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
    log_level.on_change = on_live_change
    log_level.on_select = on_live_change
    log_file.on_change = on_live_change
    websocket_port.on_submit = on_live_change
    websocket_port.on_blur = on_live_change
    monitor_dropdown.on_select = on_live_change
    position_dropdown.on_select = on_live_change
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
                ft.Text(
                    "Nível dos logs salvos no arquivo .log",
                    size=13,
                    color=ft.Colors.WHITE70,
                ),
                log_level,
                ft.Text("Nome do arquivo de log", size=13, color=ft.Colors.WHITE70),
                log_file,
                ft.Text("Porta do WebSocket", size=13, color=ft.Colors.WHITE70),
                websocket_port,
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

    hud_layout_card = ft.Container(
        content=ft.Column(
            [
                ft.Text("Posição do Overlay", size=24, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "Escolha em qual tela o HUD aparece e em qual preset ele fica.",
                    size=12,
                    color=ft.Colors.WHITE60,
                ),
                monitor_dropdown,
                position_dropdown,
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

    tabs = ft.Tabs(
        length=3,
        selected_index=0,
        animation_duration=200,
        expand=True,
        content=ft.Column(
            [
                ft.TabBar(
                    tabs=[
                        ft.Tab(label="Geral", icon=ft.Icons.TUNE),
                        ft.Tab(label="Atalhos", icon=ft.Icons.KEYBOARD),
                        ft.Tab(label="HUD", icon=ft.Icons.VISIBILITY),
                    ],
                ),
                ft.TabBarView(
                    expand=True,
                    controls=[
                        ft.Column(
                            [general_card], scroll=ft.ScrollMode.AUTO, expand=True
                        ),
                        ft.Column(
                            [hotkeys_card], scroll=ft.ScrollMode.AUTO, expand=True
                        ),
                        ft.Column(
                            [hud_layout_card, triggers_card],
                            scroll=ft.ScrollMode.AUTO,
                            expand=True,
                            spacing=14,
                        ),
                    ],
                ),
            ],
            expand=True,
            spacing=10,
        ),
    )

    page.add(
        ft.Column(
            [
                tabs,
                ft.Row(
                    [
                        ft.Icon(
                            ft.Icons.CLOUD_DONE, size=16, color=ft.Colors.GREEN_300
                        ),
                        autosave_status,
                    ],
                    spacing=8,
                ),
            ],
            spacing=12,
            expand=True,
        )
    )


if __name__ == "__main__":
    ft.run(main=main)
