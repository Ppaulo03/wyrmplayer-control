import asyncio
import logging
from typing import Optional, Set

import websockets
from websockets.server import ServerConnection

from src.core.state import AppState, MediaMetadata

logger = logging.getLogger(__name__)


class MusicWebSocketServer:
    """Gerencia o servidor WebSocket e a comunicação de texto simples com a extensão."""

    def __init__(self, state: AppState, host: str = "127.0.0.1", port: int = 8975) -> None:
        self.state = state
        self.host = host
        self.port = port
        self.clients: Set[ServerConnection] = set()
        self.command_queue: asyncio.Queue[str] = asyncio.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def start(self) -> None:
        """Inicia o servidor e o loop de broadcast."""
        self._loop = asyncio.get_running_loop()
        async with websockets.serve(self.handler, self.host, self.port):
            logger.info(f"Servidor WebSocket ouvindo em ws://{self.host}:{self.port}")
            await self._broadcast_loop()

    async def handler(self, websocket: ServerConnection) -> None:
        """Handler para conexões de entrada (Protocolo de Texto Simples)."""
        self.clients.add(websocket)
        self.state.active_connections = len(self.clients)
        logger.info(f"Extensão conectada! (Conexões ativas: {self.state.active_connections})")

        try:
            async for message in websocket:
                if isinstance(message, str):
                    self._parse_message(message)
        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.remove(websocket)
            self.state.active_connections = len(self.clients)
            logger.info(f"Extensão desconectada. (Conexões ativas: {self.state.active_connections})")

    def _time_to_seconds(self, time_str: str) -> int:
        """Converte formato MM:SS ou HH:MM:SS para segundos."""
        try:
            parts = list(map(int, time_str.split(":")))
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
            elif len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
        except Exception:
            pass
        return 0

    def _parse_message(self, message: str) -> None:
        """
        Processa as mensagens de texto simples recebidas da extensão.
        Formato esperado: "CHAVE:VALOR" (ex: "TITLE:Paradise")
        """
        if ":" not in message:
            return

        try:
            key, value = message.split(":", 1)
            key = key.upper().strip()
            value = value.strip()

            current = self.state.metadata
            title, artist, album, cover, status, volume, duration, position, progress = (
                current.title,
                current.artist,
                current.album,
                current.cover,
                current.status,
                current.volume,
                current.duration,
                current.position,
                current.progress,
            )

            updated = False
            log_meta = False

            if key == "TITLE":
                title = value
                updated = log_meta = True
            elif key == "ARTIST":
                artist = value
                updated = log_meta = True
            elif key == "ALBUM":
                album = value
                updated = True
            elif key == "COVER":
                cover = value
                updated = True
            elif key == "STATE":
                status = "Tocando" if value == "1" else "Pausado"
                updated = log_meta = True
            elif key == "VOLUME":
                try:
                    volume = int(value)
                    updated = log_meta = True
                except ValueError:
                    pass
            elif key == "DURATION":
                duration = value
                d_sec = self._time_to_seconds(duration)
                p_sec = self._time_to_seconds(position)
                progress = p_sec / d_sec if d_sec > 0 else 0.0
                updated = True
            elif key == "POSITION":
                position = value
                d_sec = self._time_to_seconds(duration)
                p_sec = self._time_to_seconds(position)
                progress = min(1.0, p_sec / d_sec) if d_sec > 0 else 0.0
                updated = True

            if updated:
                new_metadata = MediaMetadata(
                    title=title,
                    artist=artist,
                    album=album,
                    cover=cover,
                    status=status,
                    volume=volume,
                    duration=duration,
                    position=position,
                    progress=progress,
                )

                if new_metadata != self.state.metadata:
                    self.state.metadata = new_metadata
                    
                    if key == "VOLUME":
                        # Sempre atualiza a memória do último volume ativo se for > 0
                        if volume > 0:
                            self.state.last_non_zero_volume = volume
                            # Se o volume subiu externamente, assume que não está mais em "Mute"
                            if self.state.is_muted:
                                self.state.is_muted = False
                        # NOTA: Não setamos is_muted=True automaticamente para volume=0.
                        # Isso permite diferenciar o "zero manual" do "Mute" controlado pelo app.

                    # Notifica observadores
                    if self._loop:
                        self._loop.create_task(self.state.notify())

        except Exception as e:
            logger.error(f"Erro ao processar mensagem '{message}': {e}")

    async def _broadcast_loop(self) -> None:
        """Envia comandos da fila para todos os clientes conectados."""
        while True:
            command = await self.command_queue.get()
            if self.clients:
                for client in self.clients:
                    try:
                        logger.info(f"Enviando comando: {command}")
                        await client.send(command)
                    except Exception as e:
                        logger.error(f"Erro ao enviar {command}: {e}")
            self.command_queue.task_done()


    def enqueue_command(self, command: str) -> None:
        """Adiciona um comando à fila de forma thread-safe."""
        if self._loop:
            self._loop.call_soon_threadsafe(self.command_queue.put_nowait, command)
