from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, List


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
    """Estado global compartilhado da aplicação com suporte a observadores."""

    metadata: MediaMetadata = field(default_factory=MediaMetadata)
    is_muted: bool = False
    last_non_zero_volume: int = 50
    active_connections: int = 0

    # Lista de callbacks assíncronos para notificar mudanças (ex: para a UI)
    _listeners: List[Callable[[], Coroutine[Any, Any, None]]] = field(
        default_factory=list, repr=False
    )

    def on_update(self, callback: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Registra um observador para mudanças de estado."""
        self._listeners.append(callback)

    async def notify(self) -> None:
        """Notifica todos os observadores registrados."""
        for callback in self._listeners:
            try:
                await callback()
            except Exception:
                # Evita que um erro em um listener quebre o fluxo principal
                pass
