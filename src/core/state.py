import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

logger = logging.getLogger(__name__)


class StateCategory(Enum):
    """Categorias de mudança de estado para filtragem em observadores."""
    METADATA = auto()
    VOLUME = auto()
    PLAYBACK = auto()
    CONNECTION = auto()
    ALL = auto()


@dataclass(frozen=True)
class MediaMetadata:
    """Dados puros da mídia recebidos da extensão."""
    title: str = ""
    artist: str = ""
    album: str = ""
    cover: str = ""
    status: str = "Desconhecido"
    volume: int = 50
    duration: str = "0:00"
    position: str = "0:00"
    progress: float = 0.0  # 0.0 a 1.0


@dataclass
class AppState:
    """Estado global compartilhado da aplicação com suporte a observadores tipados."""

    # Dados de Domínio (Mídia)
    metadata: MediaMetadata = field(default_factory=MediaMetadata)
    is_muted: bool = False
    last_non_zero_volume: int = 50

    # Dados de Infraestrutura/Sessão
    active_connections: int = 0

    # Lista de callbacks assíncronos para notificar mudanças
    _listeners: list[Callable[[bool, StateCategory], Coroutine[Any, Any, None]]] = field(
        default_factory=list, repr=False
    )

    def on_update(self, callback: Callable[[bool, StateCategory], Coroutine[Any, Any, None]]) -> None:
        """Registra um observador para mudanças de estado."""
        self._listeners.append(callback)

    async def notify(self, major: bool = False, category: StateCategory = StateCategory.ALL) -> None:
        """Notifica os observadores sobre mudanças no estado."""
        for callback in self._listeners:
            try:
                await callback(major, category)
            except Exception as e:
                logger.error(f"Erro ao notificar observador: {e}")
