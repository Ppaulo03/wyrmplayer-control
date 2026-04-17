import asyncio
import logging

import websockets
from websockets.asyncio.server import ServerConnection

from src.core.state import AppState
from src.domain.metadata_handler import MetadataHandler

logger = logging.getLogger(__name__)


class MusicWebSocketServer:
    """Gerencia o servidor WebSocket e a comunicação de texto simples com a extensão."""

    def __init__(self, state: AppState, host: str = "127.0.0.1", port: int = 8974) -> None:
        self.state = state
        self.host = host
        self.port = port
        self.clients: set[ServerConnection] = set()
        self.command_queue: asyncio.Queue[str] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._handler = MetadataHandler(self.state)

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
            logger.info(
                f"Extensão desconectada. (Conexões ativas: {self.state.active_connections})"
            )

    def _parse_message(self, message: str) -> None:
        """Processa as mensagens recebidas da extensão via MetadataHandler."""
        result = self._handler.parse_and_apply(message)
        if result and self._loop:
            log_meta, category = result
            self._loop.create_task(self.state.notify(major=log_meta, category=category))

    async def _broadcast_loop(self) -> None:
        """Envia comandos da fila para todos os clientes conectados."""
        while True:
            command = await self.command_queue.get()
            if self.clients:
                # Iteramos sobre uma lista para evitar RuntimeError se um cliente desconectar
                for client in list(self.clients):
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
