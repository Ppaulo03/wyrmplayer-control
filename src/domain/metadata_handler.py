import logging
from typing import Optional
from src.core.state import AppState, MediaMetadata, StateCategory

logger = logging.getLogger(__name__)

class MetadataHandler:
    """Processador lógico para mensagens do protocolo textual da extensão."""
    
    def __init__(self, state: AppState):
        self.state = state

    def parse_and_apply(self, message: str) -> None:
        """
        Processa mensagens no formato "CHAVE:VALOR" e atualiza o estado.
        Retorna True se houve mudança significativa que exija notificação major.
        """
        if ":" not in message:
            return

        try:
            key, value = message.split(":", 1)
            key = key.upper().strip()
            value = value.strip()

            current = self.state.metadata
            # Campos extraídos para mutação
            title, artist, album, cover, status, volume, duration, position, progress = (
                current.title, current.artist, current.album, current.cover, 
                current.status, current.volume, current.duration, current.position, current.progress
            )

            updated = False
            log_meta = False
            category = StateCategory.ALL

            if key == "TITLE":
                title, updated, log_meta, category = value, True, True, StateCategory.METADATA
            elif key == "ARTIST":
                artist, updated, log_meta, category = value, True, True, StateCategory.METADATA
            elif key == "ALBUM":
                album, updated, category = value, True, StateCategory.METADATA
            elif key == "COVER":
                cover, updated, category = value, True, StateCategory.METADATA
            elif key == "STATE":
                status, updated, log_meta, category = (
                    "Tocando" if value == "1" else "Pausado"
                ), True, True, StateCategory.PLAYBACK
            elif key == "VOLUME":
                try:
                    volume, updated, log_meta, category = int(value), True, True, StateCategory.VOLUME
                except ValueError:
                    pass
            elif key == "DURATION":
                duration, updated = value, True
                d_sec = self._time_to_seconds(duration)
                p_sec = self._time_to_seconds(position)
                progress = p_sec / d_sec if d_sec > 0 else 0.0
            elif key == "POSITION":
                position, updated = value, True
                d_sec = self._time_to_seconds(duration)
                p_sec = self._time_to_seconds(position)
                progress = min(1.0, p_sec / d_sec) if d_sec > 0 else 0.0

            if updated:
                new_metadata = MediaMetadata(
                    title=title, artist=artist, album=album, cover=cover,
                    status=status, volume=volume, duration=duration, 
                    position=position, progress=progress
                )

                if new_metadata != self.state.metadata:
                    self.state.metadata = new_metadata
                    if key == "VOLUME" and volume > 0:
                        self.state.last_non_zero_volume = volume
                        if self.state.is_muted:
                            self.state.is_muted = False
                    
                    # A notificação real deve ser disparada pelo orquestrador (WebSocketServer)
                    # mas o handler decide os parâmetros. 
                    # Para manter compatibilidade com a estrutura atual, retornamos os dados.
                    return log_meta, category

        except Exception as e:
            logger.error(f"MetadataHandler: Erro ao processar '{message}': {e}")
        
        return None

    def _time_to_seconds(self, time_str: str) -> int:
        try:
            parts = list(map(int, time_str.split(":")))
            if len(parts) == 2: return parts[0] * 60 + parts[1]
            elif len(parts) == 3: return parts[0] * 3600 + parts[1] * 60 + parts[2]
        except Exception: pass
        return 0
