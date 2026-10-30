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
    def listening_port(self) -> int | None:
        """Retorna a porta real em que o servidor ativo está escutando."""
        return self._server.listening_port if self._server is not None else None

    def enqueue_command(self, command: str) -> None:
        """Encaminha comandos de mídia para o servidor ativo (implementa IMessenger)."""
        if self._server is not None:
            self._server.enqueue_command(command)

    async def start(self) -> bool:
        """Inicia o servidor WebSocket na porta inicial configurada."""
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
            return success

    async def _start_server_on_port(self, port: int) -> bool:
        """Cria e inicia uma nova instância do servidor na porta indicada."""
        server = MusicWebSocketServer(self.state, host=self.host, port=port)
        self._server = server
        if self.controller is not None and hasattr(self.controller, "set_messenger"):
            self.controller.set_messenger(server)

        task = asyncio.create_task(server.start())
        self._server_task = task

        # Aguarda confirmação de bind ou encerramento por falha da tarefa
        while not server.is_running and not task.done():
            await asyncio.sleep(0.005)

        if task.done():
            exc = task.exception()
            if exc:
                logger.error("Falha ao iniciar servidor WebSocket na porta %s: %s", port, exc)
            return False

        logger.info("Servidor WebSocket ativo na porta %s.", server.listening_port)
        return True

    async def stop(self) -> None:
        """Encerra definitivamente o WebSocketManager e todos os recursos supervisionados."""
        self._is_shutting_down = True
        async with self._restart_lock:
            if self._server is not None:
                try:
                    await self._server.stop()
                except Exception as e:
                    logger.warning("Falha ao encerrar servidor WebSocket no desligamento: %s", e)

            if self._server_task is not None and not self._server_task.done():
                self._server_task.cancel()
                try:
                    await self._server_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning("Falha ao aguardar tarefa servidora no desligamento: %s", e)
            self._server_task = None
