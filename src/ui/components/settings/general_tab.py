import flet as ft
from src.core.config import AppConfig

def general_tab(cfg: AppConfig, on_change: callable) -> ft.Control:
    """Aba de Ajustes Gerais."""
    
    # Controles
    volume_step = ft.Slider(min=1, max=20, divisions=19, label="{value}%", value=cfg.volume_step, on_change=on_change)
    hud_time = ft.Slider(min=1, max=10, divisions=9, label="{value}s", value=cfg.hud_display_time, on_change=on_change)
    
    log_level = ft.Dropdown(
        label="Nível de log",
        value=(cfg.log_level or "INFO").upper(),
        options=[ft.DropdownOption(key=k, text=k) for k in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]],
        expand=True,
    )
    log_level.on_select = on_change
    
    log_file = ft.TextField(
        label="Arquivo de log",
        value=cfg.log_file,
        hint_text="wyrmplayer.log",
        dense=True,
        expand=True,
        on_change=on_change
    )
    
    websocket_port = ft.TextField(
        label="Porta do WebSocket",
        value=str(cfg.websocket_port),
        hint_text="8975",
        dense=True,
        expand=True,
        keyboard_type=ft.KeyboardType.NUMBER,
        on_submit=on_change,
        on_blur=on_change
    )

    card = ft.Container(
        padding=16,
        border_radius=14,
        bgcolor="#0D1422",
        border=ft.border.all(1, "#23314A"),
        content=ft.Column(
            [
                ft.Text("Ajustes Gerais", size=24, weight=ft.FontWeight.BOLD),
                ft.Text("Passo do volume (%)", size=13, color=ft.Colors.WHITE70),
                volume_step,
                ft.Text("Tempo do HUD (segundos)", size=13, color=ft.Colors.WHITE70),
                hud_time,
                ft.Text("Nível dos logs salvos", size=13, color=ft.Colors.WHITE70),
                log_level,
                ft.Text("Nome do arquivo de log", size=13, color=ft.Colors.WHITE70),
                log_file,
                ft.Text("Porta do WebSocket", size=13, color=ft.Colors.WHITE70),
                websocket_port,
            ],
            spacing=10,
            tight=True,
        )
    )
    
    # Armazenamos referências para extração de dados no save
    card.data = {
        "volume_step": volume_step,
        "hud_display_time": hud_time,
        "log_level": log_level,
        "log_file": log_file,
        "websocket_port": websocket_port
    }
    
    return card
