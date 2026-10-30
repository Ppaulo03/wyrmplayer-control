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

        # Recursos gerenciados por ciclo
        self._server: Server | None = None
        self._stop_event: asyncio.Event | None = None
        self._broadcast_task: asyncio.Task[None] | None = None
        self._serving_task: asyncio.Task[None] | None = None

        # Sincronização de ciclo de vida
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()
        self._is_starting: bool = False
        self._is_stopping: bool = False
        self._active_cycle_id: int = 0

    @property
    def is_running(self) -> bool:
        """Indica se o servidor WebSocket está em execução e ouvindo conexões."""
        return bool(
            self._server is not None and self._server.is_serving() and not self._is_stopping
        )

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
        """Inicia o servidor e o loop de broadcast de forma sincronizada e exclusiva."""
        async with self._lifecycle_lock:
            if self.is_running:
                logger.warning(
                    "Servidor WebSocket já está ativo ou inicializando em ws://%s:%s",
                    self.host,
                    self.port,
                )
                return

            # Aguarda a conclusão definitiva de ciclo anterior que ainda esteja em cleanup
            if self._serving_task is not None and self._serving_task is not asyncio.current_task():
                if not self._serving_task.done():
                    try:
                        await self._serving_task
                    except (asyncio.CancelledError, Exception):
                        pass
                self._serving_task = None

            # Checagem de concorrência após espera de ciclo anterior
            if self.is_running or self._is_starting:
                logger.warning(
                    "Servidor WebSocket já está ativo ou inicializando em ws://%s:%s",
                    self.host,
                    self.port,
                )
                return

            self._is_starting = True
            self._loop = asyncio.get_running_loop()
            stop_event = asyncio.Event()
            self._stop_event = stop_event
            current_task = asyncio.current_task()
            self._serving_task = current_task

            self._active_cycle_id += 1
            cycle_id = self._active_cycle_id
            cycle_clients: set[ServerConnection] = set()

            async def cycle_connection_handler(websocket: ServerConnection) -> None:
                cycle_clients.add(websocket)
                try:
                    await self.handler(websocket)
                finally:
                    cycle_clients.discard(websocket)

            server: Server | None = None
            try:
                server = await websockets.serve(cycle_connection_handler, self.host, self.port)
            except asyncio.CancelledError:
                self._is_starting = False
                if self._serving_task is current_task:
                    self._serving_task = None
                if self._stop_event is stop_event:
                    self._stop_event = None
                if server is not None:
                    await self._cleanup_cycle(
                        server=server,
                        broadcast_task=None,
                        stop_event=stop_event,
                        cycle_clients=cycle_clients,
                        cycle_id=cycle_id,
                        serving_task=current_task,
                    )
                raise
            except Exception as e:
                logger.error(
                    "Falha ao abrir servidor WebSocket em %s:%s: %s", self.host, self.port, e
                )
                self._is_starting = False
                if self._serving_task is current_task:
                    self._serving_task = None
                if self._stop_event is stop_event:
                    self._stop_event = None
                if server is not None:
                    await self._cleanup_cycle(
                        server=server,
                        broadcast_task=None,
                        stop_event=stop_event,
                        cycle_clients=cycle_clients,
                        cycle_id=cycle_id,
                        serving_task=current_task,
                    )
                raise

            self._server = server
            self._is_starting = False
            actual_port = self.listening_port or self.port
            logger.info("Servidor WebSocket ouvindo em ws://%s:%s", self.host, actual_port)
            broadcast_task = asyncio.create_task(self._broadcast_loop())
            self._broadcast_task = broadcast_task

        # Execução fora do lock: aguarda sinal de parada
        try:
            await stop_event.wait()
        finally:
            await self._cleanup_cycle(
                server=server,
                broadcast_task=broadcast_task,
                stop_event=stop_event,
                cycle_clients=cycle_clients,
                cycle_id=cycle_id,
                serving_task=current_task,
            )

    async def stop(self) -> None:
        """Encerra o servidor WebSocket e desconecta clientes de forma coordenada e idempotente."""
        async with self._lifecycle_lock:
            if not self.is_running and not self._is_starting and self._serving_task is None:
                return

            self._is_stopping = True

            # Sinaliza o stop_event se ativo
            if self._stop_event is not None and not self._stop_event.is_set():
                self._stop_event.set()

            # Aguarda a tarefa do servidor concluir sua execução e cleanup
            serving_task = self._serving_task
            if serving_task is not None and serving_task is not asyncio.current_task():
                if not serving_task.done():
                    try:
                        await serving_task
                    except asyncio.CancelledError:
                        pass
                if self._serving_task is serving_task:
                    self._serving_task = None
            else:
                # Se stop foi chamado de dentro da própria serving_task ou sem serving_task
                if self._server is not None:
                    await self._cleanup_cycle(
                        server=self._server,
                        broadcast_task=self._broadcast_task,
                        stop_event=self._stop_event,
                        cycle_clients=set(self.clients),
                        cycle_id=self._active_cycle_id,
                        serving_task=self._serving_task,
                    )

            self._is_stopping = False

    async def _cleanup_cycle(
        self,
        server: Server | None,
        broadcast_task: asyncio.Task[None] | None,
        stop_event: asyncio.Event | None,
        cycle_clients: set[ServerConnection],
        cycle_id: int,
        serving_task: asyncio.Task[None] | None,
    ) -> None:
        """Limpa recursos pertencentes estritamente a este ciclo."""
        # 1. Finaliza a tarefa de broadcast deste ciclo
        task_to_cancel = broadcast_task
        if self._active_cycle_id == cycle_id and self._broadcast_task is not None:
            task_to_cancel = self._broadcast_task

        if task_to_cancel and not task_to_cancel.done():
            task_to_cancel.cancel()
            try:
                await task_to_cancel
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("Erro ao aguardar encerramento do broadcast: %s", exc)
        if self._broadcast_task is task_to_cancel or self._broadcast_task is broadcast_task:
            self._broadcast_task = None

        # 2. Fecha o socket do websockets.Server deste ciclo
        if server is not None:
            try:
                server.close()
                await server.wait_closed()
            except asyncio.CancelledError:
                try:
                    await server.wait_closed()
                except (asyncio.CancelledError, Exception):
                    pass
            except Exception as e:
                logger.warning("Exceção ao fechar websockets.Server: %s", e)

        # 3. Desconecta clientes pertencentes a este ciclo
        if cycle_clients:
            for client in list(cycle_clients):
                try:
                    await client.close(1001, "Servidor encerrando")
                except (asyncio.CancelledError, Exception):
                    pass
                self.clients.discard(client)
            cycle_clients.clear()
        self.state.active_connections = len(self.clients)

        # 4. Limpa referências internas se ainda pertencerem a este ciclo
        if self._server is server:
            self._server = None
        if self._stop_event is stop_event:
            self._stop_event = None
        if self._serving_task is serving_task:
            self._serving_task = None

        # 5. Drena comandos residuais se ainda for o ciclo ativo
        if self._active_cycle_id == cycle_id:
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
