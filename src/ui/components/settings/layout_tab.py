import flet as ft
from src.core.config import AppConfig
from src.core.display import HUD_POSITION_PRESETS, list_monitors

def layout_tab(cfg: AppConfig, on_change: callable) -> ft.Control:
    """Aba de Posicionamento e Gatilhos do HUD."""
    
    monitors = list_monitors()
    monitor_dropdown = ft.Dropdown(
        label="Tela do overlay",
        value=str(min(max(cfg.hud_monitor, 0), len(monitors) - 1)),
        options=[ft.DropdownOption(key=str(m.index), text=m.label) for m in monitors],
        expand=True,
    )
    monitor_dropdown.on_select = on_change

    position_dropdown = ft.Dropdown(
        label="Posição do overlay",
        value=(cfg.hud_position if cfg.hud_position in HUD_POSITION_PRESETS else "bottom_right"),
        options=[ft.DropdownOption(key=k, text=v) for k, v in HUD_POSITION_PRESETS.items()],
        expand=True,
    )
    position_dropdown.on_select = on_change

    t_vol = ft.Switch(label="Mudar Volume", value=cfg.triggers["volume"], on_change=on_change)
    t_meta = ft.Switch(label="Mudar Música", value=cfg.triggers["metadata"], on_change=on_change)
    t_play = ft.Switch(label="Pausar/Play", value=cfg.triggers["playback"], on_change=on_change)

    layout_card = ft.Container(
        padding=16, border_radius=14, bgcolor="#0D1422", border=ft.border.all(1, "#23314A"),
        content=ft.Column([
            ft.Text("Posição do Overlay", size=24, weight=ft.FontWeight.BOLD),
            ft.Text("Escolha a tela e o preset de posição.", size=12, color=ft.Colors.WHITE60),
            monitor_dropdown,
            position_dropdown,
        ], spacing=10, tight=True)
    )

    triggers_card = ft.Container(
        padding=16, border_radius=14, bgcolor="#0D1422", border=ft.border.all(1, "#23314A"),
        content=ft.Column([
            ft.Text("Quando Mostrar o HUD", size=24, weight=ft.FontWeight.BOLD),
            ft.Container(t_vol, bgcolor="#101A2C", border_radius=10, padding=10),
            ft.Container(t_meta, bgcolor="#101A2C", border_radius=10, padding=10),
            ft.Container(t_play, bgcolor="#101A2C", border_radius=10, padding=10),
        ], spacing=10, tight=True)
    )

    col = ft.Column([layout_card, triggers_card], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)
    
    col.data = {
        "hud_monitor": monitor_dropdown,
        "hud_position": position_dropdown,
        "volume": t_vol,
        "metadata": t_meta,
        "playback": t_play
    }
    
    return col
