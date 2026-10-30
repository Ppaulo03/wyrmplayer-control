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


@pytest.mark.asyncio
async def test_websocket_normal_startup(app_state: AppState) -> None:
    """Teste 1: Inicialização normal abre socket e reflete estado de disponibilidade."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    assert server.is_running is False
    assert server.listening_port is None

    server_task = asyncio.create_task(server.start())
    try:
        # Aguarda servidor iniciar
        for _ in range(50):
            if server.is_running:
                break
            await asyncio.sleep(0.01)

        assert server.is_running is True
        port = server.listening_port
        assert port is not None and port > 0
    finally:
        await server.stop()
        await server_task
        assert server.is_running is False


@pytest.mark.asyncio
async def test_websocket_duplicate_startup(
    app_state: AppState, caplog: pytest.LogCaptureFixture
) -> None:
    """Teste 2: Inicialização duplicada não recria sockets nem gera concorrência indevida."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    server_task = asyncio.create_task(server.start())

    try:
        for _ in range(50):
            if server.is_running:
                break
            await asyncio.sleep(0.01)
        assert server.is_running is True
        original_port = server.listening_port

        # Segunda chamada a start() com o servidor já ativo
        with caplog.at_level(logging.WARNING):
            await server.start()

        # O servidor permanece ativo no mesmo socket
        assert server.is_running is True
        assert server.listening_port == original_port
        assert any("já está ativo" in r.message for r in caplog.records)
    finally:
        await server.stop()
        await server_task


@pytest.mark.asyncio
async def test_websocket_normal_stop(app_state: AppState) -> None:
    """Teste 3: Encerramento normal libera socket e atualiza indicador de disponibilidade."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    server_task = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True
    port = server.listening_port
    assert port is not None

    # Executa encerramento
    await server.stop()
    await server_task

    assert server.is_running is False
    assert server.listening_port is None

    # Confirma que a porta foi liberada abrindo um socket na mesma porta
    test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        test_sock.bind(("127.0.0.1", port))
    finally:
        test_sock.close()


@pytest.mark.asyncio
async def test_websocket_duplicate_stop_is_idempotent(app_state: AppState) -> None:
    """Teste 4: Encerramento repetido é seguro, idempotente e não produz exceções."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    server_task = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True

    # Primeira parada
    await server.stop()
    await server_task
    assert server.is_running is False

    # Segunda e terceira paradas consecutivas
    await server.stop()
    await server.stop()
    assert server.is_running is False


@pytest.mark.asyncio
async def test_websocket_stop_before_start_is_safe(app_state: AppState) -> None:
    """Teste 5: Encerramento antes da inicialização não produz falhas e mantém estado coerente."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    assert server.is_running is False

    # Deve executar sem erros
    await server.stop()
    assert server.is_running is False
    assert server.listening_port is None


@pytest.mark.asyncio
async def test_websocket_port_in_use_error(
    app_state: AppState, caplog: pytest.LogCaptureFixture
) -> None:
    """Teste 6: Falha ao abrir porta ocupada não deixa estado falso de disponibilidade."""
    # Ocupa uma porta com um socket TCP comum
    busy_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    busy_sock.bind(("127.0.0.1", 0))
    busy_port = busy_sock.getsockname()[1]

    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=busy_port)
    try:
        with caplog.at_level(logging.ERROR):
            with pytest.raises(OSError):
                await server.start()

        assert server.is_running is False
        assert server.listening_port is None
        assert server.clients == set()
        assert app_state.active_connections == 0
        assert any("Falha ao abrir servidor WebSocket" in r.message for r in caplog.records)
    finally:
        busy_sock.close()


@pytest.mark.asyncio
async def test_websocket_recovery_after_port_in_use_failure(app_state: AppState) -> None:
    """Teste 7: Servidor recupera e inicia com sucesso após a liberação da porta."""
    busy_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    busy_sock.bind(("127.0.0.1", 0))
    port = busy_sock.getsockname()[1]

    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=port)
    with pytest.raises(OSError):
        await server.start()
    assert server.is_running is False

    # Libera a porta e tenta iniciar novamente
    busy_sock.close()
    await asyncio.sleep(0.02)

    server_task = asyncio.create_task(server.start())
    try:
        for _ in range(50):
            if server.is_running:
                break
            await asyncio.sleep(0.01)

        assert server.is_running is True
        assert server.listening_port == port
    finally:
        await server.stop()
        await server_task
        assert server.is_running is False


@pytest.mark.asyncio
async def test_websocket_client_connected_during_shutdown(app_state: AppState) -> None:
    """Teste 8: Encerramento com cliente conectado fecha conexão e limpa contadores."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    server_task = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    port = server.listening_port
    assert port is not None

    async with connect(f"ws://127.0.0.1:{port}") as client_ws:
        # Aguarda handshake de conexão no servidor
        for _ in range(50):
            if app_state.active_connections == 1:
                break
            await asyncio.sleep(0.01)
        assert app_state.active_connections == 1
        assert len(server.clients) == 1

        # Encerra o servidor enquanto o cliente ainda está conectado
        await server.stop()
        await server_task

        assert server.is_running is False
        assert app_state.active_connections == 0
        assert len(server.clients) == 0

        # O cliente deve receber sinal de fechamento
        with pytest.raises(websockets.ConnectionClosed):
            await client_ws.recv()


@pytest.mark.asyncio
async def test_websocket_no_leaked_tasks_or_sockets(app_state: AppState) -> None:
    """Teste 9: Ausência de tarefas órfãs ou recursos retidos após encerramento."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    server_task = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True

    # Encerra o servidor
    await server.stop()
    await server_task

    # Confirma que a tarefa de broadcast foi finalizada
    assert server._broadcast_task is None
    assert server._server is None
    assert len(server.clients) == 0
    assert server.command_queue.empty()


@pytest.mark.asyncio
async def test_websocket_enqueue_and_broadcast_command(app_state: AppState) -> None:
    """Teste 10: Comandos enfileirados são entregues aos clientes conectados."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    server_task = asyncio.create_task(server.start())

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

        # Enfileira comando
        server.enqueue_command("setVolume 75")
        received = await asyncio.wait_for(client_ws.recv(), timeout=2.0)
        assert received == "setVolume 75"

    await server.stop()
    await server_task


@pytest.mark.asyncio
async def test_websocket_receive_message_and_update_state(app_state: AppState) -> None:
    """Teste 11: Mensagens recebidas do cliente atualizam o estado via MetadataHandler."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    server_task = asyncio.create_task(server.start())

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

        await client_ws.send("TITLE:WyrmTrack")
        await client_ws.send("ARTIST:WyrmArtist")

        for _ in range(50):
            if app_state.metadata.title == "WyrmTrack":
                break
            await asyncio.sleep(0.01)

        assert app_state.metadata.title == "WyrmTrack"
        assert app_state.metadata.artist == "WyrmArtist"

    await server.stop()
    await server_task


@pytest.mark.asyncio
async def test_websocket_task_cancellation_shuts_down_cleanly(app_state: AppState) -> None:
    """Teste 12: Cancelamento assíncrono da tarefa de start encerra o servidor de forma limpa."""
    server = MusicWebSocketServer(app_state, host="127.0.0.1", port=0)
    server_task = asyncio.create_task(server.start())

    for _ in range(50):
        if server.is_running:
            break
        await asyncio.sleep(0.01)
    assert server.is_running is True

    # Cancela diretamente a task (simulando shutdown da aplicação)
    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass

    assert server.is_running is False
    assert server.listening_port is None
