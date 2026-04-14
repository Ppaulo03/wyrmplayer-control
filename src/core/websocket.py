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

            # Como as mensagens chegam um campo por vez, precisamos preservar o estado dos outros campos
            current = self.state.metadata
            title, artist, album, cover, status, volume = (
                current.title,
                current.artist,
                current.album,
                current.cover,
                current.status,
                current.volume,
            )

            updated = False

            if key == "TITLE":
                title = value
                updated = True
            elif key == "ARTIST":
                artist = value
                updated = True
            elif key == "ALBUM":
                album = value
                updated = True
            elif key == "COVER":
                cover = value
                updated = True
            elif key == "STATE":
                # 1 = Tocando, 2 = Pausado (Padrão WNP Redux para texto)
                status = "Tocando" if value == "1" else "Pausado"
                updated = True
            elif key == "VOLUME":
                try:
                    volume = int(value)
                    updated = True
                except ValueError:
                    pass

            if updated:
                new_metadata = MediaMetadata(
                    title=title,
                    artist=artist,
                    album=album,
                    cover=cover,
                    status=status,
                    volume=volume,
                )

                if new_metadata != self.state.metadata:
                    self.state.metadata = new_metadata
                    # Sincroniza estado de mute com o volume real recebido
                    if volume == 0:
                        self.state.is_muted = True
                    elif volume > 0 and self.state.is_muted:
                        self.state.is_muted = False

                    logger.info(
                        f"META: {title} - {artist} ({status}) | Vol: {volume}%"
                    )
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
