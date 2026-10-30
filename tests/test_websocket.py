import asyncio
import logging
import socket
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import websockets
from websockets.asyncio.client import connect
from websockets.asyncio.server import Server

from src.core.config import ConfigManager
from src.core.state import AppState
from src.core.websocket import MusicWebSocketServer
from src.services.config_watcher import ConfigWatcher
from src.services.websocket_manager import WebSocketManager


@pytest.fixture
def app_state() -> AppState:
    """Fixture para estado isolado da aplicação."""
    return AppState()


# ==============================================================================
# Grupo A: Inicializações Concorrentes e Tratamento de Bind (R01)
# ==============================================================================


@pytest.mark.asyncio
async def test_websocket_concurrent_startup_ephemeral_port(
    app_state: AppState, caplog: pytest.LogCaptureFixture
) -> None:
    """Grupo A: Duas chamadas concorrentes com port=0 abrem apenas um socket."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)

    with caplog.at_level(logging.WARNING):
        task_a = asyncio.create_task(server.start())
        task_b = asyncio.create_task(server.start())

        # Aguarda inicialização
        for _ in range(50):
            if server.is_running:
                break
            await asyncio.sleep(0.01)

        assert server.is_running is True
        port = server.listening_port
        assert port is not None and port > 0

        # Apenas uma tarefa de broadcast foi criada
        assert server._broadcast_task is not None
        assert not server._broadcast_task.done()

        # Aviso registrado informando que já estava ativo/inicializando
        assert any("já está ativo ou inicializando" in r.message for r in caplog.records)

        # Encerramento seguro
        await server.stop()
        await asyncio.gather(task_a, task_b, return_exceptions=True)

        assert server.is_running is False
        assert server._broadcast_task is None
        assert server._server is None


@pytest.mark.asyncio
async def test_websocket_concurrent_startup_fixed_port(app_state: AppState) -> None:
    """Grupo A: Duas chamadas concorrentes com porta fixa abrem apenas um servidor."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    fixed_port = sock.getsockname()[1]
    sock.close()
    await asyncio.sleep(0.02)

    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=fixed_port)

    task_1 = asyncio.create_task(server.start())
    task_2 = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)

    assert server.is_running is True
    assert server.listening_port == fixed_port

    await server.stop()
    await asyncio.gather(task_1, task_2, return_exceptions=True)
    assert server.is_running is False


@pytest.mark.asyncio
async def test_websocket_failure_during_concurrent_bind_preserves_valid_server(
    app_state: AppState,
) -> None:
    """Grupo A: Falha concorrente preserva a integridade do servidor principal."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    task_valid = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)

    assert server.is_running is True
    valid_server_ref = server._server
    assert valid_server_ref is not None

    orig_serve = websockets.serve

    async def failing_serve(*args: object, **kwargs: object) -> None:
        raise OSError("Falha simulada de bind em tentativa concorrente")

    websockets.serve = failing_serve  # type: ignore[assignment]
    try:
        await server.start()
        assert server.is_running is True
        assert server._server is valid_server_ref
    finally:
        websockets.serve = orig_serve  # type: ignore[assignment]

    await server.stop()
    await task_valid
    assert server.is_running is False


@pytest.mark.asyncio
async def test_websocket_bind_cancellation_cleans_transient_state(app_state: AppState) -> None:
    """R01 - Teste 1: Cancelamento durante bind limpa estados transitórios e propaga erro."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    orig_serve = websockets.serve
    bind_entered = asyncio.Event()
    unblock_barrier = asyncio.Event()

    async def intercepting_serve(*args: Any, **kwargs: Any) -> Server:
        bind_entered.set()
        await unblock_barrier.wait()
        return await orig_serve(*args, **kwargs)

    websockets.serve = intercepting_serve  # type: ignore[assignment]
    task = asyncio.create_task(server.start())

    try:
        await bind_entered.wait()
        assert server._is_starting is True
        assert server._serving_task is task

        # Cancela diretamente a tarefa durante o bind
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        # Estados transitórios devem estar completamente restaurados
        assert server.is_running is False
        assert server._is_starting is False
        assert server._serving_task is None
        assert server._stop_event is None
        assert server._server is None
        assert server._broadcast_task is None
    finally:
        unblock_barrier.set()
        websockets.serve = orig_serve  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_websocket_recovery_after_bind_cancellation(app_state: AppState) -> None:
    """R01 - Teste 2: Mesma instância recupera e inicia normalmente após bind cancelado."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    orig_serve = websockets.serve
    bind_entered = asyncio.Event()
    unblock_barrier = asyncio.Event()

    async def intercepting_serve(*args: Any, **kwargs: Any) -> Server:
        bind_entered.set()
        await unblock_barrier.wait()
        return await orig_serve(*args, **kwargs)

    websockets.serve = intercepting_serve  # type: ignore[assignment]
    task1 = asyncio.create_task(server.start())

    try:
        await bind_entered.wait()
        task1.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task1
    finally:
        unblock_barrier.set()
        websockets.serve = orig_serve  # type: ignore[assignment]

    # Stop subsequente é seguro e idempotente
    await server.stop()
    assert server.is_running is False

    # Nova inicialização na mesma instância
    task2 = asyncio.create_task(server.start())
    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)

    assert server.is_running is True
    assert server.listening_port is not None and server.listening_port > 0

    await server.stop()
    await task2
    assert server.is_running is False


# ==============================================================================
# Grupo B: Encerramento, Cancelamento e Isolamento de Recursos (R02)
# ==============================================================================


@pytest.mark.asyncio
async def test_websocket_shutdown_releases_server_and_broadcast(app_state: AppState) -> None:
    """Grupo B: Shutdown normal libera o socket e finaliza a tarefa de broadcast."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    task = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)

    assert server.is_running is True
    broadcast_task = server._broadcast_task
    assert broadcast_task is not None and not broadcast_task.done()

    await server.stop()
    await task

    assert server.is_running is False
    assert server._server is None
    assert server._broadcast_task is None
    assert broadcast_task.done()


@pytest.mark.asyncio
async def test_websocket_repeated_shutdown_is_idempotent(app_state: AppState) -> None:
    """Grupo B: Chamadas repetidas a stop() são estritamente idempotentes."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    task = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True

    await server.stop()
    await task
    assert server.is_running is False

    # Repetições não geram erro nem alteram estado
    await server.stop()
    await server.stop()
    assert server.is_running is False


@pytest.mark.asyncio
async def test_websocket_delayed_broadcast_cleanup_waits_for_completion(
    app_state: AppState,
) -> None:
    """Grupo B: stop() aguarda a finalização completa mesmo com cleanup demorado."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    task = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True

    orig_broadcast = server._broadcast_task
    assert orig_broadcast is not None
    broadcast_completed = asyncio.Event()

    async def instrumented_broadcast() -> None:
        try:
            await orig_broadcast
        except asyncio.CancelledError:
            await asyncio.sleep(0.08)
            broadcast_completed.set()
            raise

    server._broadcast_task = asyncio.create_task(instrumented_broadcast())

    await server.stop()
    await task

    assert broadcast_completed.is_set()
    assert server.is_running is False
    assert server._broadcast_task is None


@pytest.mark.asyncio
async def test_websocket_rapid_restart_same_instance(app_state: AppState) -> None:
    """Grupo B: Reinicialização rápida da mesma instância funciona perfeitamente."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)

    # Ciclo 1
    t1 = asyncio.create_task(server.start())
    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True
    port_1 = server.listening_port
    assert port_1 is not None and port_1 > 0

    await server.stop()
    await t1
    assert server.is_running is False

    # Ciclo 2 imediato
    t2 = asyncio.create_task(server.start())
    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True
    port_2 = server.listening_port
    assert port_2 is not None

    await server.stop()
    await t2
    assert server.is_running is False


@pytest.mark.asyncio
async def test_websocket_direct_cancellation_of_active_server(app_state: AppState) -> None:
    """R02 - Teste 5: Cancelamento de servidor ativo desconecta cliente e limpa recursos."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    task = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    port = server.listening_port
    assert port is not None

    async with connect(f"ws://127.0.0.1:{port}") as client_ws:
        for _ in range(50):
            if app_state.active_connections == 1:
                break
            await asyncio.sleep(0.01)
        assert app_state.active_connections == 1

        # Cancela diretamente a tarefa principal
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        # Conexão remota deve ter sido encerrada
        with pytest.raises(websockets.ConnectionClosed):
            await client_ws.recv()

    assert server.is_running is False
    assert app_state.active_connections == 0
    assert server._server is None
    assert server._broadcast_task is None


@pytest.mark.asyncio
async def test_websocket_extended_cleanup_isolates_new_server(app_state: AppState) -> None:
    """R02 - Teste 6: Cleanup prolongado de A não modifica recursos nem clientes do ciclo B."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    task_a = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True

    # Instrumenta wait_closed do servidor A para atrasar deterministamente
    server_a = server._server
    assert server_a is not None
    orig_wait_closed = server_a.wait_closed
    cleanup_entered = asyncio.Event()
    cleanup_delay_gate = asyncio.Event()

    async def delayed_wait_closed() -> None:
        cleanup_entered.set()
        await cleanup_delay_gate.wait()
        await orig_wait_closed()

    server_a.wait_closed = delayed_wait_closed  # type: ignore[method-assign]

    # Cancela Servidor A diretamente
    task_a.cancel()
    await cleanup_entered.wait()

    # Neste ponto, Servidor A está no meio de seu cleanup.
    # Solicita inicialização do Servidor B na mesma instância
    task_b = asyncio.create_task(server.start())
    await asyncio.sleep(0.02)

    # Servidor B não pode ter assumido recursos prematuramente
    assert server.is_running is False
    assert not task_b.done()

    # Libera conclusão do cleanup de A
    cleanup_delay_gate.set()

    with pytest.raises(asyncio.CancelledError):
        await task_a

    # Agora B deve conseguir assumir e iniciar com sucesso
    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)

    assert server.is_running is True
    port_b = server.listening_port
    assert port_b is not None

    # Conecta um cliente real ao servidor B
    async with connect(f"ws://127.0.0.1:{port_b}") as client_b:
        for _ in range(50):
            if app_state.active_connections == 1:
                break
            await asyncio.sleep(0.01)
        assert app_state.active_connections == 1

        # Envia e recebe dados para comprovar integridade de B
        server.enqueue_command("ping_cycle_b")
        received = await asyncio.wait_for(client_b.recv(), timeout=2.0)
        assert received == "ping_cycle_b"

        # Confirma que cliente B continua conectado
        assert app_state.active_connections == 1

    # Encerra Servidor B normalmente
    await server.stop()
    await task_b

    assert server.is_running is False
    assert server._server is None
    assert server._broadcast_task is None
    assert app_state.active_connections == 0


@pytest.mark.asyncio
async def test_websocket_reference_integrity_across_lifecycles(app_state: AppState) -> None:
    """R02 - Teste 7: Integridade e coerência estrita de todas as referências internas."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)

    # Estado inicial
    assert server._server is None
    assert server._broadcast_task is None
    assert server._serving_task is None
    assert server._stop_event is None
    assert len(server.clients) == 0
    assert server.state.active_connections == 0

    t1 = asyncio.create_task(server.start())
    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)

    assert server._server is not None
    assert server._broadcast_task is not None
    assert server._serving_task is t1
    assert server._stop_event is not None

    await server.stop()
    await t1

    assert server._server is None
    assert server._broadcast_task is None
    assert server._serving_task is None
    assert server._stop_event is None
    assert len(server.clients) == 0
    assert server.state.active_connections == 0


@pytest.mark.asyncio
async def test_websocket_explicit_stop_during_cancellation_cleanup(app_state: AppState) -> None:
    """R02 - Teste 8: stop() durante cleanup de cancelamento não trava nem encerra prematuro."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    task = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True

    server_obj = server._server
    assert server_obj is not None
    orig_wc = server_obj.wait_closed
    entered = asyncio.Event()
    gate = asyncio.Event()

    async def delayed_wc() -> None:
        entered.set()
        await gate.wait()
        await orig_wc()

    server_obj.wait_closed = delayed_wc  # type: ignore[method-assign]

    # Cancela tarefa principal
    task.cancel()
    await entered.wait()

    # Dispara stop() concorrentemente com o cleanup em andamento
    stop_op = asyncio.create_task(server.stop())
    await asyncio.sleep(0.02)

    # stop() deve estar aguardando a finalização da serving_task
    assert not stop_op.done()

    # Libera o gate
    gate.set()

    await stop_op
    with pytest.raises(asyncio.CancelledError):
        await task

    assert server.is_running is False
    assert server._server is None


@pytest.mark.asyncio
async def test_websocket_restart_after_full_cancellation_cycle(app_state: AppState) -> None:
    """R02 - Teste 9: Instância reinicia e opera perfeitamente após cancelamento completo."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    t1 = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True

    t1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t1

    assert server.is_running is False

    # Novo ciclo normal
    t2 = asyncio.create_task(server.start())
    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)

    assert server.is_running is True
    port = server.listening_port
    assert port is not None

    async with connect(f"ws://127.0.0.1:{port}") as client_ws:
        server.enqueue_command("test_after_cancel")
        msg = await asyncio.wait_for(client_ws.recv(), timeout=2.0)
        assert msg == "test_after_cancel"

    await server.stop()
    await t2
    assert server.is_running is False


@pytest.mark.asyncio
async def test_websocket_restart_during_active_cleanup(app_state: AppState) -> None:
    """Grupo B: Iniciar o servidor durante encerramento ativo é serializado."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    t1 = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True

    # Inicia o stop e dispara concorrentemente um novo start
    stop_op = asyncio.create_task(server.stop())
    await asyncio.sleep(0.002)
    start_op2 = asyncio.create_task(server.start())

    # Aguarda o stop do primeiro ciclo
    await stop_op
    await t1

    # O segundo ciclo deve assumir e ficar ativo
    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)

    assert server.is_running is True
    assert server._broadcast_task is not None and not server._broadcast_task.done()

    await server.stop()
    await start_op2
    assert server.is_running is False


# ==============================================================================
# Grupo C: Transições e Recuperação de Falhas
# ==============================================================================


@pytest.mark.asyncio
async def test_websocket_stop_during_startup_bind(app_state: AppState) -> None:
    """Grupo C: Parada solicitada durante o bind fecha o socket sem deixar leaks."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    orig_serve = websockets.serve

    async def delayed_serve(*args: object, **kwargs: object) -> websockets.asyncio.server.Server:
        await asyncio.sleep(0.05)
        res = await orig_serve(*args, **kwargs)  # type: ignore[arg-type]
        return res

    websockets.serve = delayed_serve  # type: ignore[assignment]
    try:
        start_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.01)

        await server.stop()
        await start_task
    finally:
        websockets.serve = orig_serve  # type: ignore[assignment]

    assert server.is_running is False
    assert server._server is None


@pytest.mark.asyncio
async def test_websocket_recovery_after_startup_failure(app_state: AppState) -> None:
    """Grupo C / R01 Teste 3: Servidor recupera após falha convencional de bind."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    busy_port = sock.getsockname()[1]

    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=busy_port)

    with pytest.raises(OSError):
        await server.start()
    assert server.is_running is False

    sock.close()
    await asyncio.sleep(0.02)

    task = asyncio.create_task(server.start())
    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)

    assert server.is_running is True
    assert server.listening_port == busy_port

    await server.stop()
    await task
    assert server.is_running is False


# ==============================================================================
# Grupo D: Regressões Existentes, Protocolo e Códigos de Fechamento
# ==============================================================================


@pytest.mark.asyncio
async def test_websocket_stop_before_start_is_safe_noop(app_state: AppState) -> None:
    """Grupo D: Chamar stop() antes de start() é um no-op seguro."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    assert server.is_running is False
    await server.stop()
    assert server.is_running is False


@pytest.mark.asyncio
async def test_websocket_client_connected_shutdown_code_1001(app_state: AppState) -> None:
    """Grupo D / OBS03: Cliente conectado recebe código de encerramento 1001 no shutdown."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    task = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    port = server.listening_port
    assert port is not None

    async with connect(f"ws://127.0.0.1:{port}") as client_ws:
        for _ in range(50):
            if app_state.active_connections == 1:
                break
            await asyncio.sleep(0.01)
        assert app_state.active_connections == 1

        await server.stop()
        await task

        assert server.is_running is False
        assert app_state.active_connections == 0

        with pytest.raises(websockets.ConnectionClosed) as exc_info:
            await client_ws.recv()

        assert exc_info.value.rcvd is not None
        assert exc_info.value.rcvd.code == 1001


@pytest.mark.asyncio
async def test_websocket_commands_and_metadata_roundtrip(app_state: AppState) -> None:
    """Grupo D: Broadcast de comandos e recepção de metadados continuam íntegros."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    task = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    port = server.listening_port
    assert port is not None

    async with connect(f"ws://127.0.0.1:{port}") as client_ws:
        for _ in range(50):
            if app_state.active_connections == 1:
                break
            await asyncio.sleep(0.01)

        server.enqueue_command("playPause")
        received = await asyncio.wait_for(client_ws.recv(), timeout=2.0)
        assert received == "playPause"

        await client_ws.send("TITLE:WyrmSyncSong")
        await client_ws.send("VOLUME:90")

        for _ in range(50):
            if app_state.metadata.title == "WyrmSyncSong":
                break
            await asyncio.sleep(0.01)

        assert app_state.metadata.title == "WyrmSyncSong"
        assert app_state.metadata.volume == 90

    await server.stop()
    await task


# ==============================================================================
# Grupo E: Propagação de Cancelamentos e Supervisão de Cleanup (EST-03.1.3)
# ==============================================================================


@pytest.mark.asyncio
async def test_websocket_cancellation_of_pending_startup_preserves_prior_cleanup(
    app_state: AppState,
) -> None:
    """R01 - Teste 2 & 3: Cancelar inicialização pendente propaga erro e não abre socket."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)

    # 1. Inicia o servidor A
    task_a = asyncio.create_task(server.start())
    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True

    # 2. Atrasa o cleanup de A deterministicamente
    server_a = server._server
    assert server_a is not None
    orig_wait_closed = server_a.wait_closed
    cleanup_entered = asyncio.Event()
    cleanup_gate = asyncio.Event()

    async def delayed_wait_closed() -> None:
        cleanup_entered.set()
        await cleanup_gate.wait()
        await orig_wait_closed()

    server_a.wait_closed = delayed_wait_closed  # type: ignore[method-assign]

    # Inicia encerramento de A
    task_a.cancel()
    await cleanup_entered.wait()
    assert server.is_cleaning_up is True

    # 3. Solicita inicialização B enquanto A está em cleanup
    task_b = asyncio.create_task(server.start())
    await asyncio.sleep(0.02)
    assert not task_b.done()

    # 4. Cancela B enquanto aguarda A
    task_b.cancel()

    # 5. B deve terminar imediatamente com CancelledError
    with pytest.raises(asyncio.CancelledError):
        await task_b

    # 6. B não deve ter aberto socket nem criado broadcast
    assert server.is_running is False
    assert server._broadcast_task is None

    # 7. Cleanup de A não foi interrompido e continua supervisionado
    assert server.is_cleaning_up is True
    assert not task_a.done()

    # 8. Libera A para concluir
    cleanup_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await task_a

    # 9. Aguarda cleanup_task de A finalizar completamente
    if server._cleanup_task is not None:
        await server._cleanup_task

    assert server.is_cleaning_up is False
    assert server._server is None
    assert server._serving_task is None
    assert server._stop_event is None
    assert len(server.clients) == 0
    assert server.state.active_connections == 0

    # 10. Agora inicia C normalmente e confirma que opera perfeitamente
    task_c = asyncio.create_task(server.start())
    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)

    assert server.is_running is True
    assert server.listening_port is not None and server.listening_port > 0

    await server.stop()
    await task_c
    assert server.is_running is False


@pytest.mark.asyncio
async def test_websocket_repeated_cancellations_during_wait_closed_preserves_supervision(
    app_state: AppState,
) -> None:
    """R02 - Teste 6: Cancelamentos repetidos durante cleanup mantêm supervisão íntegra."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    task = asyncio.create_task(server.start())
    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True

    server_obj = server._server
    assert server_obj is not None
    orig_wc = server_obj.wait_closed
    entered = asyncio.Event()
    gate = asyncio.Event()

    async def delayed_wc() -> None:
        entered.set()
        await gate.wait()
        await orig_wc()

    server_obj.wait_closed = delayed_wc  # type: ignore[method-assign]

    # Primeiro cancelamento
    task.cancel()
    await entered.wait()

    # Cleanup está em andamento e supervisionado
    assert server.is_cleaning_up is True

    # Cancelamentos adicionais repetidos
    task.cancel()
    task.cancel()

    # Confirma que a instância NÃO declarou encerramento prematuro
    assert server.is_cleaning_up is True
    assert server.is_running is False

    # Libera conclusão de wait_closed
    gate.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    if server._cleanup_task is not None:
        await server._cleanup_task

    assert server.is_cleaning_up is False
    assert server._server is None
    assert server._broadcast_task is None

    # Confirma que a mesma instância pode ser reutilizada
    task2 = asyncio.create_task(server.start())
    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True
    await server.stop()
    await task2
    assert server.is_running is False


@pytest.mark.asyncio
async def test_websocket_stop_external_timeout_propagates_and_supervises_cleanup(
    app_state: AppState,
) -> None:
    """OBS01 - Teste 9: Timeout em stop() propaga TimeoutError e preserva cleanup."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    task = asyncio.create_task(server.start())
    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True

    server_obj = server._server
    assert server_obj is not None
    orig_wc = server_obj.wait_closed
    entered = asyncio.Event()
    gate = asyncio.Event()

    async def delayed_wc() -> None:
        entered.set()
        await gate.wait()
        await orig_wc()

    server_obj.wait_closed = delayed_wc  # type: ignore[method-assign]

    # Executa stop() com prazo curto que expira
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(server.stop(), timeout=0.03)

    # O timeout foi comunicado ao chamador, mas o cleanup continua supervisionado
    assert server.is_cleaning_up is True

    # Libera a finalização
    gate.set()
    await task

    if server._cleanup_task is not None:
        await server._cleanup_task

    assert server.is_cleaning_up is False
    assert server._server is None
    assert server._broadcast_task is None


@pytest.mark.asyncio
async def test_websocket_stop_waiting_task_cancellation_does_not_abort_cleanup(
    app_state: AppState,
) -> None:
    """OBS01 - Teste 10: Cancelar observador de stop() não aborta o cleanup original."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    task = asyncio.create_task(server.start())
    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True

    server_obj = server._server
    assert server_obj is not None
    orig_wc = server_obj.wait_closed
    entered = asyncio.Event()
    gate = asyncio.Event()

    async def delayed_wc() -> None:
        entered.set()
        await gate.wait()
        await orig_wc()

    server_obj.wait_closed = delayed_wc  # type: ignore[method-assign]

    # Inicia stop() em tarefa observadora 1
    stop_task_1 = asyncio.create_task(server.stop())
    await entered.wait()

    # Tarefa observadora 2 também aguarda stop()
    stop_task_2 = asyncio.create_task(server.stop())
    await asyncio.sleep(0.01)

    # Cancela somente a tarefa observadora 2
    stop_task_2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop_task_2

    # Cleanup continua ativo e supervisionado
    assert server.is_cleaning_up is True
    assert not stop_task_1.done()

    # Libera finalização
    gate.set()
    await stop_task_1
    await task

    assert server.is_cleaning_up is False
    assert server._server is None


# ==============================================================================
# Grupo F: Reinicialização Dinâmica e Alterações de Porta (EST-03.2)
# ==============================================================================


@pytest.mark.asyncio
async def test_websocket_manager_normal_port_change_and_client_connection(
    app_state: AppState,
) -> None:
    """R01 - Testes 1, 2, 3: Reinício normal com troca de porta, liberação e nova conexão."""
    manager = WebSocketManager(app_state, initial_port=0)
    started = await manager.start()
    assert started is True
    assert manager.is_running is True

    port_a = manager.listening_port
    assert port_a is not None and port_a > 0

    # Cliente conecta na porta A
    async with connect(f"ws://127.0.0.1:{port_a}") as client_a:
        manager.enqueue_command("cmd_on_a")
        msg = await asyncio.wait_for(client_a.recv(), timeout=2.0)
        assert msg == "cmd_on_a"

    # Aloca uma porta B livre
    sock_b = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock_b.bind(("127.0.0.1", 0))
    port_b = sock_b.getsockname()[1]
    sock_b.close()
    await asyncio.sleep(0.01)

    # Reinicia na porta B
    success = await manager.restart(port_b)
    assert success is True
    assert manager.is_running is True
    assert manager.listening_port == port_b

    # Porta A deve estar liberada e não receber conexões
    with pytest.raises(OSError):
        async with connect(f"ws://127.0.0.1:{port_a}"):
            pass

    # Porta B aceita conexões e comandos normais
    async with connect(f"ws://127.0.0.1:{port_b}") as client_b:
        manager.enqueue_command("cmd_on_b")
        msg = await asyncio.wait_for(client_b.recv(), timeout=2.0)
        assert msg == "cmd_on_b"

    await manager.stop()
    assert manager.is_running is False


@pytest.mark.asyncio
async def test_websocket_manager_occupied_port_failure_and_recovery(
    app_state: AppState,
) -> None:
    """R02 - Testes 4, 5: Porta nova ocupada falha de forma controlada e recupera quando livre."""
    manager = WebSocketManager(app_state, initial_port=0)
    await manager.start()
    assert manager.is_running is True

    # Ocupa uma porta B externamente
    sock_b = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock_b.bind(("127.0.0.1", 0))
    port_b = sock_b.getsockname()[1]

    try:
        # Tenta reiniciar na porta ocupada
        success = await manager.restart(port_b)
        assert success is False
        assert manager.is_running is False
        assert manager.listening_port is None
    finally:
        # Libera a porta B
        sock_b.close()

    await asyncio.sleep(0.02)

    # Nova tentativa na porta B agora desocupada
    recovery = await manager.restart(port_b)
    assert recovery is True
    assert manager.is_running is True
    assert manager.listening_port == port_b

    await manager.stop()
    assert manager.is_running is False


@pytest.mark.asyncio
async def test_websocket_manager_successive_and_concurrent_port_changes(
    app_state: AppState,
) -> None:
    """R03 - Testes 6, 7: Alterações sucessivas e concorrentes convergem deterministicamente."""
    manager = WebSocketManager(app_state, initial_port=0)
    await manager.start()
    port_a = manager.listening_port
    assert port_a is not None

    # Reserva duas portas livres
    s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s1.bind(("127.0.0.1", 0))
    port_b = s1.getsockname()[1]
    s1.close()

    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2.bind(("127.0.0.1", 0))
    port_c = s2.getsockname()[1]
    s2.close()
    await asyncio.sleep(0.01)

    # Dispara solicitações sucessivas e concorrentes de restart
    res_b, res_c = await asyncio.gather(
        manager.restart(port_b),
        manager.restart(port_c),
    )

    # Ambas devem concluir sem exceções
    assert res_b is True or res_c is True
    assert manager.is_running is True

    # O servidor ativo final deve corresponder à porta final consolidada
    assert manager.listening_port == port_c

    # Portas intermediárias e anteriores não devem reter conexões
    for p in [port_a, port_b]:
        if p != port_c:
            with pytest.raises(OSError):
                async with connect(f"ws://127.0.0.1:{p}"):
                    pass

    # A porta consolidada opera normalmente
    async with connect(f"ws://127.0.0.1:{port_c}") as client_c:
        manager.enqueue_command("ping_c")
        msg = await asyncio.wait_for(client_c.recv(), timeout=2.0)
        assert msg == "ping_c"

    await manager.stop()
    assert manager.is_running is False


@pytest.mark.asyncio
async def test_websocket_manager_shutdown_during_pending_restart_prevents_new_server(
    app_state: AppState,
) -> None:
    """R04 - Teste 8: Encerramento global durante reinício pendente impede novo socket."""
    manager = WebSocketManager(app_state, initial_port=0)
    await manager.start()
    assert manager.is_running is True

    # Atrasa deterministamente o encerramento do servidor atual
    curr_server = manager.server
    assert curr_server is not None
    orig_server_obj = curr_server._server
    assert orig_server_obj is not None
    orig_wc = orig_server_obj.wait_closed
    entered_wc = asyncio.Event()
    gate_wc = asyncio.Event()

    async def delayed_wc() -> None:
        entered_wc.set()
        await gate_wc.wait()
        await orig_wc()

    orig_server_obj.wait_closed = delayed_wc  # type: ignore[method-assign]

    # Reserva porta para o restart
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    target_port = s.getsockname()[1]
    s.close()
    await asyncio.sleep(0.01)

    # Dispara o restart em background
    restart_task = asyncio.create_task(manager.restart(target_port))
    await entered_wc.wait()

    # Dispara o shutdown definitivo do manager
    stop_task = asyncio.create_task(manager.stop())
    await asyncio.sleep(0.01)

    # Libera a finalização do servidor antigo
    gate_wc.set()

    res_restart = await restart_task
    await stop_task

    # O restart não deve ter aberto o novo servidor
    assert res_restart is False
    assert manager.is_running is False
    assert manager.listening_port is None

    # A porta alvo não deve estar escutando
    with pytest.raises(OSError):
        async with connect(f"ws://127.0.0.1:{target_port}"):
            pass


@pytest.mark.asyncio
async def test_websocket_config_watcher_dynamic_port_change_and_invalid_handling(
    app_state: AppState, tmp_path: Any
) -> None:
    """R05 - Testes 9, 10, 11: ConfigWatcher detecta troca de porta e ignora JSON inválido."""
    config_file = tmp_path / "settings.json"
    s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s1.bind(("127.0.0.1", 0))
    port_1 = s1.getsockname()[1]
    s1.close()

    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2.bind(("127.0.0.1", 0))
    port_2 = s2.getsockname()[1]
    s2.close()
    await asyncio.sleep(0.01)

    config_file.write_text(f'{{"websocket_port": {port_1}}}', encoding="utf-8")
    cfg_mgr = ConfigManager(config_file=str(config_file))
    cfg_mgr.load()

    manager = WebSocketManager(app_state, initial_port=port_1)
    await manager.start()
    assert manager.listening_port == port_1

    exit_event = asyncio.Event()

    async def on_port_change(p: int) -> None:
        await manager.restart(p)

    mock_hotkeys = MagicMock()
    mock_hotkeys.is_input_desktop_accessible.return_value = True
    mock_hud = MagicMock()
    mock_hud.show_hud = AsyncMock()

    watcher = ConfigWatcher(
        hotkeys=mock_hotkeys,
        hud=mock_hud,
        state=app_state,
        config=cfg_mgr,
        on_websocket_port_change=on_port_change,
        on_spotify_integration_change=MagicMock(),
    )
    watcher_task = asyncio.create_task(watcher.start(exit_event))

    try:
        # 1. Altera porta para port_2
        await asyncio.sleep(0.1)
        config_file.write_text(f'{{"websocket_port": {port_2}}}', encoding="utf-8")

        for _ in range(50):
            if manager.listening_port == port_2:
                break
            await asyncio.sleep(0.05)

        assert manager.listening_port == port_2
        assert manager.is_running is True

        # 2. Escreve conteúdo inválido no settings.json (não deve alterar porta nem quebrar)
        await asyncio.sleep(0.1)
        config_file.write_text('{"websocket_port": INVALID_JSON', encoding="utf-8")
        await asyncio.sleep(0.6)

        assert manager.listening_port == port_2
        assert manager.is_running is True

        # 3. Alteração mantendo a mesma porta (não dispara novo restart desnecessário)
        config_file.write_text(
            f'{{"websocket_port": {port_2}, "log_level": "DEBUG"}}', encoding="utf-8"
        )
        await asyncio.sleep(0.6)
        assert manager.listening_port == port_2

    finally:
        exit_event.set()
        await watcher_task
        await manager.stop()


@pytest.mark.asyncio
async def test_websocket_stop_requested_flag_aborts_slow_bind_before_serving(
    app_state: AppState,
) -> None:
    """R06 - Teste 12: Flag _stop_requested aborta bind lento e impede socket órfão."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    orig_serve = websockets.serve

    bind_in_progress = asyncio.Event()
    unblock_bind = asyncio.Event()

    async def slow_serve(*args: Any, **kwargs: Any) -> Server:
        bind_in_progress.set()
        await unblock_bind.wait()
        return await orig_serve(*args, **kwargs)

    websockets.serve = slow_serve  # type: ignore[assignment]
    try:
        start_task = asyncio.create_task(server.start())
        await bind_in_progress.wait()

        # Timeout curto simulando cancelamento antes de obter o lock de stop
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(server.stop(), timeout=0.01)

        # Confirma que _stop_requested foi marcado
        assert server._stop_requested is True

        # Libera o bind para finalizar
        unblock_bind.set()
        await start_task

        # O servidor detecta _stop_requested, encerra o socket alocado e não fica escutando
        assert server.is_running is False
        assert server._server is None

    finally:
        unblock_bind.set()
        websockets.serve = orig_serve  # type: ignore[assignment]
