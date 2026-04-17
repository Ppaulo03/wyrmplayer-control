from typing import Protocol, runtime_checkable

@runtime_checkable
class IMessenger(Protocol):
    """Protocolo para envio de comandos de mídia para um backend externo."""
    
    def enqueue_command(self, command: str) -> None:
        """Adiciona um comando à fila de processamento do transporte."""
        ...
