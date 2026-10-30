import asyncio
import logging

import websockets
from websockets.asyncio.server import Server, ServerConnection

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
        self._server: Server | None = None
        self._stop_event: asyncio.Event | None = None
        self._broadcast_task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        """Indica se o servidor WebSocket está em execução e ouvindo conexões."""
        return bool(self._server is not None and self._server.is_serving())

    @property
    def listening_port(self) -> int | None:
        """Retorna a porta real em que o socket está ouvindo, útil quando port=0."""
        if self._server is not None and self._server.sockets:
            for s in self._server.sockets:
                sockname = s.getsockname()
                if isinstance(sockname, tuple) and len(sockname) >= 2:
                    return int(sockname[1])
        return self.port if self.is_running else None

    async def start(self) -> None:
        """Inicia o servidor e o loop de broadcast."""
        if self.is_running:
            logger.warning("Servidor WebSocket já está ativo em ws://%s:%s", self.host, self.port)
            return

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()

        try:
            server = await websockets.serve(self.handler, self.host, self.port)
        except Exception as e:
            logger.error("Falha ao abrir servidor WebSocket em %s:%s: %s", self.host, self.port, e)
            self._server = None
            raise

        self._server = server
        actual_port = self.listening_port or self.port
        logger.info("Servidor WebSocket ouvindo em ws://%s:%s", self.host, actual_port)

        self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            if self._broadcast_task and not self._broadcast_task.done():
                self._broadcast_task.cancel()
                try:
                    await self._broadcast_task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.warning("Erro ao aguardar encerramento do broadcast: %s", exc)
            self._broadcast_task = None
            await self._close_server(server)

    async def stop(self) -> None:
        """Encerra o servidor WebSocket e desconecta todos os clientes de forma idempotente."""
        if not self.is_running and self._stop_event is None:
            return

        if self._stop_event is not None and not self._stop_event.is_set():
            self._stop_event.set()

        if self._server is not None:
            await self._close_server(self._server)

    async def _close_server(self, server: Server) -> None:
        """Fecha o socket do servidor e todas as conexões ativas."""
        try:
            server.close()
            await server.wait_closed()
        except Exception as e:
            logger.warning("Exceção ao fechar websockets.Server: %s", e)
        finally:
            if self.clients:
                for client in list(self.clients):
                    try:
                        await client.close(1001, "Servidor encerrando")
                    except Exception:
                        pass
                self.clients.clear()
            self.state.active_connections = 0
            if self._server is server:
                self._server = None
            # Drena comandos residuais da fila para evitar envio de dados obsoletos em reinício
            while not self.command_queue.empty():
                try:
                    self.command_queue.get_nowait()
                    self.command_queue.task_done()
                except (asyncio.QueueEmpty, ValueError):
                    break

    async def handler(self, websocket: ServerConnection) -> None:
        """Handler para conexões de entrada (Protocolo de Texto Simples)."""
        self.clients.add(websocket)
        self.state.active_connections = len(self.clients)
        logger.info("Extensão conectada! (Conexões ativas: %s)", self.state.active_connections)

        try:
            async for message in websocket:
                if isinstance(message, str):
                    self._parse_message(message)
        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            self.state.active_connections = len(self.clients)
            logger.info(
                "Extensão desconectada. (Conexões ativas: %s)", self.state.active_connections
            )

    def _parse_message(self, message: str) -> None:
        """Processa as mensagens recebidas da extensão via MetadataHandler."""
        result = self._handler.parse_and_apply(message)
        if result and self._loop and self._loop.is_running():
            log_meta, category = result
            self._loop.create_task(self.state.notify(major=log_meta, category=category))

    async def _broadcast_loop(self) -> None:
        """Envia comandos da fila para todos os clientes conectados."""
        while True:
            command = await self.command_queue.get()
            try:
                if self.clients:
                    # Iteramos sobre uma lista para evitar RuntimeError se um cliente desconectar
                    for client in list(self.clients):
                        try:
                            logger.info("Enviando comando: %s", command)
                            await client.send(command)
                        except Exception as e:
                            logger.error("Erro ao enviar %s: %s", command, e)
            finally:
                self.command_queue.task_done()

    def enqueue_command(self, command: str) -> None:
        """Adiciona um comando à fila de forma thread-safe."""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self.command_queue.put_nowait, command)
