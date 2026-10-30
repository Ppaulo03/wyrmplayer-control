import asyncio
import logging
import socket

import pytest
import websockets
from websockets.asyncio.client import connect

from src.core.state import AppState
from src.core.websocket import MusicWebSocketServer


@pytest.fixture
def app_state() -> AppState:
    """Fixture para estado isolado da aplicação."""
    return AppState()


# ==============================================================================
# Grupo A: Inicializações Concorrentes (R01)
# ==============================================================================


@pytest.mark.asyncio
async def test_websocket_concurrent_startup_ephemeral_port(
    app_state: AppState, caplog: pytest.LogCaptureFixture
) -> None:
    """Grupo A - Teste 1: Duas chamadas concorrentes com port=0 abrem apenas um socket."""
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
    """Grupo A - Teste 2: Duas chamadas concorrentes com porta fixa abrem apenas um servidor."""
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
    """Grupo A - Teste 3: Falha concorrente preserva servidor válido.

    Garante que uma falha em uma tentativa concorrente não apague nem
    corrompa a referência do servidor principal.
    """
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    task_valid = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)

    assert server.is_running is True
    valid_server_ref = server._server
    assert valid_server_ref is not None

    # Simula uma chamada concorrente em que o websockets.serve falharia se tentado
    orig_serve = websockets.serve

    async def failing_serve(*args: object, **kwargs: object) -> None:
        raise OSError("Falha simulada de bind em tentativa concorrente")

    websockets.serve = failing_serve  # type: ignore[assignment]
    try:
        # Chama start diretamente; a proteção exclusiva deve ignorar sem invocar o serve quebrado
        await server.start()
        # O servidor original permanece intacto e funcional
        assert server.is_running is True
        assert server._server is valid_server_ref
    finally:
        websockets.serve = orig_serve  # type: ignore[assignment]

    await server.stop()
    await task_valid
    assert server.is_running is False


# ==============================================================================
# Grupo B: Encerramento e Reinicialização (R02)
# ==============================================================================


@pytest.mark.asyncio
async def test_websocket_shutdown_releases_server_and_broadcast(app_state: AppState) -> None:
    """Grupo B - Teste 4: Shutdown normal libera o socket e finaliza a tarefa de broadcast."""
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
    """Grupo B - Teste 5: Chamadas repetidas a stop() são estritamente idempotentes."""
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

    # Repetições
    await server.stop()
    await server.stop()
    assert server.is_running is False


@pytest.mark.asyncio
async def test_websocket_delayed_broadcast_cleanup_waits_for_completion(
    app_state: AppState,
) -> None:
    """Grupo B - Teste 6: stop() aguarda a finalização completa mesmo com cleanup demorado."""
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
            await asyncio.sleep(0.08)  # Atraso determinístico de encerramento
            broadcast_completed.set()
            raise

    server._broadcast_task = asyncio.create_task(instrumented_broadcast())

    # Invoca stop()
    await server.stop()
    await task

    # stop() deve ter aguardado a conclusão completa da tarefa de broadcast
    assert broadcast_completed.is_set()
    assert server.is_running is False
    assert server._broadcast_task is None


@pytest.mark.asyncio
async def test_websocket_rapid_restart_same_instance(app_state: AppState) -> None:
    """Grupo B - Teste 7: Reinicialização rápida da mesma instância funciona perfeitamente."""
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
async def test_websocket_restart_during_active_cleanup(app_state: AppState) -> None:
    """Grupo B - Teste 8: Iniciar o servidor durante encerramento ativo é serializado."""
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
# Grupo C: Transições e Recuperação
# ==============================================================================


@pytest.mark.asyncio
async def test_websocket_stop_during_startup_bind(app_state: AppState) -> None:
    """Grupo C - Teste 9: Parada solicitada durante o bind fecha o socket sem deixar leaks."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    orig_serve = websockets.serve

    async def delayed_serve(*args: object, **kwargs: object) -> websockets.asyncio.server.Server:
        await asyncio.sleep(0.05)  # Atraso durante o bind
        res = await orig_serve(*args, **kwargs)  # type: ignore[arg-type]
        return res

    websockets.serve = delayed_serve  # type: ignore[assignment]
    try:
        start_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.01)  # start está no meio do delayed_serve

        # stop() chamado durante o bind em andamento
        await server.stop()
        await start_task
    finally:
        websockets.serve = orig_serve  # type: ignore[assignment]

    # O servidor não pode permanecer aberto
    assert server.is_running is False
    assert server._server is None


@pytest.mark.asyncio
async def test_websocket_recovery_after_startup_failure(app_state: AppState) -> None:
    """Grupo C - Teste 10: Servidor recupera após falha de porta ocupada e inicia normalmente."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    busy_port = sock.getsockname()[1]

    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=busy_port)

    with pytest.raises(OSError):
        await server.start()
    assert server.is_running is False

    # Libera a porta e reinicia a mesma instância
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
# Grupo D: Regressões Existentes e Verificação de Protocolo
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
    """Grupo D / OBS02: Cliente conectado recebe código de encerramento 1001 no shutdown."""
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

        # Encerra o servidor
        await server.stop()
        await task

        assert server.is_running is False
        assert app_state.active_connections == 0

        # Verifica código 1001 (Going Away)
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

        # 1. Enfileira comando do app para a extensão
        server.enqueue_command("playPause")
        received = await asyncio.wait_for(client_ws.recv(), timeout=2.0)
        assert received == "playPause"

        # 2. Envia metadados da extensão para o app
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
