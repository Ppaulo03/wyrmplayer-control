from dataclasses import dataclass
from src.infrastructure.win32 import get_monitors_info

@dataclass(frozen=True)
class MonitorArea:
    """Representa um monitor e sua work area no Windows."""
    index: int
    label: str
    left: int
    top: int
    right: int
    bottom: int
    work_left: int
    work_top: int
    work_right: int
    work_bottom: int

    @property
    def width(self) -> int:
        return self.work_right - self.work_left

    @property
    def height(self) -> int:
        return self.work_bottom - self.work_top


HUD_POSITION_PRESETS: dict[str, str] = {
    "bottom_right": "Inferior direita",
    "bottom_left": "Inferior esquerda",
    "top_right": "Superior direita",
    "top_left": "Superior esquerda",
    "top_center": "Superior central",
    "bottom_center": "Inferior central",
    "center": "Centralizado",
}


def list_monitors() -> list[MonitorArea]:
    """Lista os monitores disponíveis abstraindo a complexidade do Win32."""
    monitors_data = get_monitors_info()
    
    if not monitors_data:
        # Fallback para caso de erro ou não-Windows
        return [MonitorArea(0, "Tela 1", 0, 0, 1920, 1080, 0, 0, 1920, 1080)]

    return [
        MonitorArea(
            index=m["index"],
            label=f"Tela {m['index'] + 1} ({m['work_rect'][2] - m['work_rect'][0]}x{m['work_rect'][3] - m['work_rect'][1]})",
            left=m["monitor_rect"][0],
            top=m["monitor_rect"][1],
            right=m["monitor_rect"][2],
            bottom=m["monitor_rect"][3],
            work_left=m["work_rect"][0],
            work_top=m["work_rect"][1],
            work_right=m["work_rect"][2],
            work_bottom=m["work_rect"][3],
        ) for m in monitors_data
    ]


def get_monitor_by_index(index: int) -> MonitorArea:
    """Retorna o monitor solicitado ou o primeiro monitor disponível."""
    monitors = list_monitors()
    if index < 0 or index >= len(monitors):
        return monitors[0]
    return monitors[index]


def resolve_hud_position(
    monitor: MonitorArea,
    preset: str,
    hud_width: int,
    hud_height: int,
    margin: int = 20,
) -> tuple[int, int]:
    """Resolve coordenadas da janela do HUD com base no monitor e preset."""
    left, top, right, bottom = monitor.work_left, monitor.work_top, monitor.work_right, monitor.work_bottom

    max_left = max(left + 8, right - hud_width - 8)
    max_top = max(top + 8, bottom - hud_height - 8)

    positions = {
        "bottom_left": (left + margin, bottom - hud_height - margin),
        "top_right": (right - hud_width - margin, top + margin),
        "top_left": (left + margin, top + margin),
        "top_center": (left + (monitor.width - hud_width) // 2, top + margin),
        "bottom_center": (left + (monitor.width - hud_width) // 2, bottom - hud_height - margin),
        "center": (left + (monitor.width - hud_width) // 2, top + (monitor.height - hud_height) // 2),
    }

    target_left, target_top = positions.get(preset, (right - hud_width - margin, bottom - hud_height - margin))

    return max(left + 8, min(target_left, max_left)), max(top + 8, min(target_top, max_top))
