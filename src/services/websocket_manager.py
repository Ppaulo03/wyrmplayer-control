import asyncio
import logging
from typing import Any

from src.core.state import AppState
from src.core.websocket import MusicWebSocketServer

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Orquestrador do ciclo de vida e reinicialização dinâmica do servidor WebSocket."""

    def __init__(
        self,
        state: AppState,
        initial_port: int,
        controller: Any = None,
        host: str = "127.0.0.1",
    ) -> None:
        self.state = state
        self.host = host
        self.current_port = initial_port
        self.controller = controller

        self._server: MusicWebSocketServer | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._restart_lock: asyncio.Lock = asyncio.Lock()
        self._target_port: int | None = None
        self._is_shutting_down: bool = False
        self._shutdown_task: asyncio.Task[None] | None = None

    @property
    def server(self) -> MusicWebSocketServer | None:
        """Retorna a instância do servidor WebSocket atualmente gerenciada."""
        return self._server

    @property
    def is_running(self) -> bool:
        """Indica se há um servidor WebSocket ativo e escutando conexões."""
        return bool(
            self._server is not None and self._server.is_running and not self._is_shutting_down
        )

    @property
    def is_shutting_down(self) -> bool:
        """Indica se o gerenciador está em processo de encerramento definitivo."""
        return self._is_shutting_down

    @property
    def is_cleaning_up(self) -> bool:
        """Indica se há uma rotina de encerramento em execução em background."""
        return bool(self._shutdown_task is not None and not self._shutdown_task.done())

    @property
    def listening_port(self) -> int | None:
        """Retorna a porta real em que o servidor ativo está escutando."""
        if self._is_shutting_down or self._server is None:
            return None
        return self._server.listening_port

    def enqueue_command(self, command: str) -> None:
        """Encaminha comandos de mídia para o servidor ativo (implementa IMessenger)."""
        server = self._server
        if server is not None and server.is_running and not self._is_shutting_down:
            server.enqueue_command(command)
        else:
            logger.debug(
                "Comando descartado: nenhum servidor WebSocket operacional (cmd=%s)",
                command,
            )

    async def start(self) -> bool:
        """Inicia o servidor WebSocket na porta inicial configurada."""
        if self._is_shutting_down:
            logger.warning("Tentativa de iniciar WebSocketManager durante shutdown.")
            return False

        async with self._restart_lock:
            if self._is_shutting_down:
                logger.warning("Tentativa de iniciar WebSocketManager durante shutdown.")
                return False

            if self.is_running:
                logger.warning("WebSocketManager já está ativo na porta %s.", self.listening_port)
                return True

            return await self._start_server_on_port(self.current_port)

    async def restart(self, new_port: int) -> bool:
        """Reinicia o servidor WebSocket na nova porta de forma coordenada e atômica."""
        if self._is_shutting_down:
            logger.warning("Reinício ignorado: aplicação em encerramento.")
            return False

        self._target_port = new_port
        async with self._restart_lock:
            if self._is_shutting_down:
                logger.warning("Reinício ignorado: aplicação em encerramento.")
                return False

            # Se já está rodando na porta desejada e esta é a porta final solicitada
            if (
                self.is_running
                and self.listening_port == new_port
                and self._target_port == new_port
            ):
                logger.info(
                    "WebSocket já está em execução na porta %s. Reinício ignorado.",
                    new_port,
                )
                return True

            logger.info("Reiniciando servidor WebSocket para a porta %s...", new_port)

            # 1. Encerra o servidor anterior
            if self._server is not None:
                try:
                    await self._server.stop()
                except Exception as e:
                    logger.warning("Falha ao encerrar servidor anterior: %s", e)

            # 2. Aguarda a tarefa servidora anterior concluir
            if self._server_task is not None and not self._server_task.done():
                self._server_task.cancel()
                try:
                    await self._server_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning("Falha ao aguardar tarefa servidora anterior: %s", e)
            self._server_task = None
            self._server = None

            # 3. Verifica se durante o encerramento houve shutdown global
            if self._is_shutting_down:
                logger.info(
                    "Shutdown global detectado durante encerramento. Abortando nova inicialização."
                )
                return False

            # 4. Inicia o novo servidor na porta alvo consolidada
            target = self._target_port if self._target_port is not None else new_port
            self.current_port = target
            success = await self._start_server_on_port(target)

            if self._is_shutting_down:
                if self._server is not None:
                    await self._cleanup_server_instance(self._server, self._server_task)
                    self._server = None
                    self._server_task = None
                return False

            return success

    async def _start_server_on_port(self, port: int) -> bool:
        """Cria e inicia uma nova instância do servidor na porta indicada."""
        if self._is_shutting_down:
            return False

        server = MusicWebSocketServer(self.state, host=self.host, port=port)
        self._server = server

        task = asyncio.create_task(server.start())
        self._server_task = task

        try:
            while not server.is_running and not task.done():
                if self._is_shutting_down:
                    break
                await asyncio.sleep(0.005)

            if self._is_shutting_down:
                logger.info(
                    "Shutdown solicitado durante inicialização na porta %s. Cancelando.",
                    port,
                )
                await self._cleanup_server_instance(server, task)
                self._server = None
                self._server_task = None
                return False

            if task.done():
                exc = task.exception()
                if exc:
                    logger.error("Falha ao iniciar servidor WebSocket na porta %s: %s", port, exc)
                await self._cleanup_server_instance(server, task)
                self._server = None
                self._server_task = None
                return False

            logger.info("Servidor WebSocket ativo na porta %s.", server.listening_port)
            return True

        except asyncio.CancelledError:
            logger.info("Inicialização do WebSocket na porta %s foi cancelada.", port)
            await asyncio.shield(self._cleanup_server_instance(server, task))
            self._server = None
            self._server_task = None
            raise

    async def _cleanup_server_instance(
        self,
        server: MusicWebSocketServer | None,
        task: asyncio.Task[None] | None,
    ) -> None:
        """Finaliza com segurança uma instância de servidor e sua respectiva tarefa."""
        if server is not None:
            server._stop_requested = True

        if task is not None and not task.done():
            task.cancel()

        if server is not None:
            try:
                await server.stop()
            except Exception as e:
                logger.warning("Falha ao encerrar servidor WebSocket: %s", e)

        if task is not None:
            if not task.done():
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning("Falha ao aguardar tarefa servidora: %s", e)
            elif task.done():
                try:
                    task.exception()
                except asyncio.CancelledError:
                    pass

    def _ensure_shutdown_task(self) -> asyncio.Task[None]:
        """Garante a existência de uma tarefa de shutdown supervisionada e atômica."""
        if self._shutdown_task is None or self._shutdown_task.done():
            self._shutdown_task = asyncio.create_task(self._do_shutdown())
        return self._shutdown_task

    async def _do_shutdown(self) -> None:
        """Executa a rotina de shutdown de recursos sob proteção do lock."""
        async with self._restart_lock:
            server = self._server
            task = self._server_task
            self._server = None
            self._server_task = None

            await self._cleanup_server_instance(server, task)

    async def stop(self) -> None:
        """Encerra definitivamente o WebSocketManager e todos os recursos supervisionados."""
        self._is_shutting_down = True
        shutdown_task = self._ensure_shutdown_task()
        try:
            await asyncio.shield(shutdown_task)
        except asyncio.CancelledError:
            logger.info("Observador de stop() cancelado; cleanup do gerenciador prossegue.")
            raise
