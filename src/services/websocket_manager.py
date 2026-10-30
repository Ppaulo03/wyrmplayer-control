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
        self._cleanup_task: asyncio.Task[None] | None = None
        self._cleanup_server: MusicWebSocketServer | None = None
        self._cleanup_server_task: asyncio.Task[None] | None = None

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
        shutdown_busy = bool(self._shutdown_task is not None and not self._shutdown_task.done())
        cleanup_busy = bool(self._cleanup_task is not None and not self._cleanup_task.done())
        return shutdown_busy or cleanup_busy

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

    async def _wait_for_pending_cleanup(self) -> None:
        """Aguarda a conclusão de qualquer cleanup de geração anterior pendente."""
        task = self._cleanup_task
        if task is not None and not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Exceção durante espera de cleanup anterior: %s", e)

    def _schedule_cleanup(
        self,
        server: MusicWebSocketServer | None,
        task: asyncio.Task[None] | None,
    ) -> asyncio.Task[None]:
        """Agenda e supervisiona o cleanup de uma geração de servidor de forma idempotente."""
        if self._cleanup_task is not None:
            if self._cleanup_server is server and self._cleanup_server_task is task:
                return self._cleanup_task
            if not self._cleanup_task.done():
                prev_task = self._cleanup_task
                cleanup_task = asyncio.create_task(
                    self._run_chained_cleanup(prev_task, server, task)
                )
                self._cleanup_task = cleanup_task
                self._cleanup_server = server
                self._cleanup_server_task = task
                return cleanup_task

        cleanup_task = asyncio.create_task(self._run_supervised_cleanup(server, task))
        self._cleanup_task = cleanup_task
        self._cleanup_server = server
        self._cleanup_server_task = task
        return cleanup_task

    async def _run_chained_cleanup(
        self,
        prev_task: asyncio.Task[None],
        server: MusicWebSocketServer | None,
        task: asyncio.Task[None] | None,
    ) -> None:
        """Aguarda a tarefa de cleanup anterior antes de executar o cleanup da nova geração."""
        try:
            await asyncio.shield(prev_task)
        except Exception as e:
            logger.warning("Falha ao aguardar cleanup de geração anterior: %s", e)
        await self._run_supervised_cleanup(server, task)

    async def _run_supervised_cleanup(
        self,
        server: MusicWebSocketServer | None,
        task: asyncio.Task[None] | None,
    ) -> None:
        """Executa o encerramento do servidor e restaura referências por identidade."""
        cleanup_failed = False
        try:
            await self._cleanup_server_instance(server, task)
        except Exception as e:
            cleanup_failed = True
            logger.error("Falha crítica durante cleanup do servidor WebSocket: %s", e)
            raise
        finally:
            if not cleanup_failed:
                if self._server is server:
                    self._server = None
                if self._server_task is task:
                    self._server_task = None

    async def start(self) -> bool:
        """Inicia o servidor WebSocket na porta inicial configurada."""
        if self._is_shutting_down:
            logger.warning("Tentativa de iniciar WebSocketManager durante shutdown.")
            return False

        async with self._restart_lock:
            if self._is_shutting_down:
                logger.warning("Tentativa de iniciar WebSocketManager durante shutdown.")
                return False

            await self._wait_for_pending_cleanup()
            if self._is_shutting_down:
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

            await self._wait_for_pending_cleanup()
            if self._is_shutting_down:
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

            # 1. Encerra o servidor anterior de forma supervisionada
            if self._server is not None:
                old_server = self._server
                old_task = self._server_task
                cleanup_task = self._schedule_cleanup(old_server, old_task)
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    logger.info("Reinício cancelado durante encerramento do servidor anterior.")
                    raise

            # 2. Verifica se durante o encerramento houve shutdown global
            if self._is_shutting_down:
                logger.info(
                    "Shutdown global detectado durante encerramento. Abortando nova inicialização."
                )
                return False

            # 3. Inicia o novo servidor na porta alvo consolidada
            target = self._target_port if self._target_port is not None else new_port
            self.current_port = target
            success = await self._start_server_on_port(target)

            if self._is_shutting_down:
                if self._server is not None:
                    cleanup_task = self._schedule_cleanup(self._server, self._server_task)
                    await asyncio.shield(cleanup_task)
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
                cleanup_task = self._schedule_cleanup(server, task)
                await asyncio.shield(cleanup_task)
                return False

            if task.done():
                exc = task.exception()
                if exc:
                    logger.error("Falha ao iniciar servidor WebSocket na porta %s: %s", port, exc)
                cleanup_task = self._schedule_cleanup(server, task)
                await asyncio.shield(cleanup_task)
                return False

            logger.info("Servidor WebSocket ativo na porta %s.", server.listening_port)
            return True

        except asyncio.CancelledError:
            logger.info("Inicialização do WebSocket na porta %s foi cancelada.", port)
            self._schedule_cleanup(server, task)
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
            await self._wait_for_pending_cleanup()
            server = self._server
            task = self._server_task
            self._server = None
            self._server_task = None

            if server is not None or task is not None:
                cleanup_task = self._schedule_cleanup(server, task)
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    raise

    async def stop(self) -> None:
        """Encerra definitivamente o WebSocketManager e todos os recursos supervisionados."""
        self._is_shutting_down = True
        shutdown_task = self._ensure_shutdown_task()
        try:
            await asyncio.shield(shutdown_task)
        except asyncio.CancelledError:
            logger.info("Observador de stop() cancelado; cleanup do gerenciador prossegue.")
            raise
