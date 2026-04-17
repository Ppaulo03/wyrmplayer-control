import pytest
import asyncio
from src.core.state import AppState, StateCategory

@pytest.mark.asyncio
async def test_state_notification():
    """Valida se o sistema de observadores notifica as mudanças corretamente."""
    state = AppState()
    
    # Mock de observador
    received = []
    async def observer(major, category):
        received.append((major, category))
    
    state.on_update(observer)
    
    # Dispara uma notificação
    await state.notify(major=True, category=StateCategory.METADATA)
    
    assert len(received) == 1
    major, category = received[0]
    assert major is True
    assert category == StateCategory.METADATA

@pytest.mark.asyncio
async def test_multiple_observers():
    """Garante que múltiplos observadores recebam a mesma notificação."""
    state = AppState()
    count = 0
    
    async def obs1(m, c): nonlocal count; count += 1
    async def obs2(m, c): nonlocal count; count += 1
    
    state.on_update(obs1)
    state.on_update(obs2)
    
    await state.notify()
    assert count == 2
