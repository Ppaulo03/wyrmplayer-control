import asyncio
import ctypes
import logging
from typing import Optional

import flet as ft

from src.core.state import AppState

logger = logging.getLogger(__name__)


class MusicHUD:
    """Interface visual (HUD) expandida com duração da música e progresso."""

    def __init__(self, state: AppState) -> None:
        self.state = state
        self.page: Optional[ft.Page] = None
        self._hide_task: Optional[asyncio.Task] = None

    def _get_screen_resolution(self) -> tuple[int, int]:
        """Obtém a resolução da tela principal."""
        try:
            user32 = ctypes.windll.user32
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        except Exception:
            return 1920, 1080

    def _force_window_stealth(self) -> None:
        """Arranca a barra de título e bordas via Win32 API."""
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, "Music HUD")
            if hwnd:
                style = ctypes.windll.user32.GetWindowLongW(hwnd, -16)
                new_style = style & ~0x00C00000 
                new_style = new_style & ~0x00040000 
                new_style = new_style & ~0x00080000 
                ctypes.windll.user32.SetWindowLongW(hwnd, -16, new_style)
                
                ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
                ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex_style | 0x00000080 | 0x00000008)
                
                ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004)
        except Exception as e:
            logger.warning(f"Win32 Brute Force Error: {e}")

    async def main(self, page: ft.Page) -> None:
        """Configuração da página Flet com suporte a metadados estendidos."""
        self.page = page
        self.page.title = "Music HUD"
        
        self.page.window.bgcolor = "#00000000"
        self.page.bgcolor = "#00000000"
        
        self.page.window.frameless = True
        self.page.window.always_on_top = True
        self.page.window.skip_task_bar = True
        self.page.window.resizable = False
        
        # Aumentamos um pouco a altura para acomodar o progresso
        width = 450
        height = 160 
        self.page.window.width = width
        self.page.window.height = height
        
        screen_w, screen_h = self._get_screen_resolution()
        target_left = screen_w - width - 20
        target_top = screen_h - height - 60
        
        self.page.window.left = target_left
        self.page.window.top = target_top
        
        self.page.window.opacity = 0.0
        self.page.window.visible = True

        # --- Elementos da UI ---
        self.cover = ft.Image(
            src=self.state.metadata.cover or "https://via.placeholder.com/80",
            width=80,
            height=80,
            border_radius=10,
            fit=ft.BoxFit.COVER,
        )

        self.title = ft.Text(
            self.state.metadata.title or "Nenhuma música",
            size=18,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.WHITE,
            overflow=ft.TextOverflow.ELLIPSIS,
            max_lines=1,
        )

        self.artist = ft.Text(
            self.state.metadata.artist or "Aguardando player...",
            size=14,
            color=ft.Colors.WHITE70,
            overflow=ft.TextOverflow.ELLIPSIS,
            max_lines=1,
        )

        self.track_progress_bar = ft.ProgressBar(
            value=self.state.metadata.progress,
            width=300,
            color=ft.Colors.AMBER,
            bgcolor=ft.Colors.WHITE10,
        )

        self.time_text = ft.Text(
            f"{self.state.metadata.position} / {self.state.metadata.duration}",
            size=12,
            color=ft.Colors.WHITE54,
        )

        self.volume_indicator = ft.Row(
            [
                ft.Icon(ft.Icons.VOLUME_UP, size=12, color=ft.Colors.WHITE54),
                ft.Text(f"{self.state.metadata.volume}%", size=12, color=ft.Colors.WHITE54),
            ],
            spacing=5,
        )

        self.container = ft.Container(
            content=ft.Row(
                [
                    self.cover,
                    ft.VerticalDivider(width=10, color=ft.Colors.TRANSPARENT),
                    ft.Column(
                        [
                            self.title,
                            self.artist,
                            ft.Container(height=5),
                            self.track_progress_bar,
                            ft.Row(
                                [
                                    self.time_text,
                                    ft.Container(expand=True),
                                    self.volume_indicator,
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        expand=True,
                    ),
                ],
                alignment=ft.MainAxisAlignment.START,
            ),
            padding=15,
            bgcolor="#E6050505",
            border_radius=15,
            border=ft.border.all(1, ft.Colors.WHITE10),
        )

        self.page.add(self.container)
        self.state.on_update(self.update_ui)
        
        self.page.update()
        
        # Força posições
        self.page.window.left = target_left
        self.page.window.top = target_top
        self.page.update()

        await asyncio.sleep(0.5)
        self._force_window_stealth()

    async def update_ui(self) -> None:
        """Atualiza a interface com metadados de tempo."""
        if not self.page:
            return

        self.title.value = self.state.metadata.title or "Nenhuma música"
        self.artist.value = self.state.metadata.artist or "Desconhecido"
        self.cover.src = self.state.metadata.cover or "https://via.placeholder.com/80"
        
        # Atualiza Tempo e Progresso
        self.track_progress_bar.value = self.state.metadata.progress
        self.time_text.value = f"{self.state.metadata.position} / {self.state.metadata.duration}"
        
        # Atualiza Volume
        self.volume_indicator.controls[1].value = f"{self.state.metadata.volume}%"
        self.volume_indicator.controls[0].color = ft.Colors.RED if self.state.is_muted else ft.Colors.WHITE54

        await self.show_hud()

    async def show_hud(self) -> None:
        """Exibe o HUD."""
        if not self.page:
            return

        if self._hide_task:
            self._hide_task.cancel()

        self.page.window.opacity = 1.0
        self.page.update()
        
        self._force_window_stealth()
        self._hide_task = asyncio.create_task(self._hide_after_delay(3))

    async def _hide_after_delay(self, seconds: int) -> None:
        """Fade out."""
        try:
            await asyncio.sleep(seconds)
            if self.page:
                steps = 10
                for i in range(steps, -1, -1):
                    self.page.window.opacity = i / steps
                    self.page.update()
                    await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass
