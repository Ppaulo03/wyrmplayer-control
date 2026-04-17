import pytest
from src.core.state import AppState, StateCategory
from src.domain.metadata_handler import MetadataHandler

@pytest.fixture
def app_state():
    return AppState()

@pytest.fixture
def handler(app_state):
    return MetadataHandler(app_state)

def test_parse_title_change(handler, app_state):
    """Verifica se a mudança de título é processada corretamente."""
    result = handler.parse_and_apply("TITLE:New Song")
    assert result is not None
    log_meta, category = result
    assert log_meta is True
    assert category == StateCategory.METADATA
    assert app_state.metadata.title == "New Song"

def test_parse_artist_change(handler, app_state):
    """Verifica se a mudança de artista é processada corretamente."""
    handler.parse_and_apply("ARTIST:New Artist")
    assert app_state.metadata.artist == "New Artist"

def test_parse_volume_change(handler, app_state):
    """Verifica se a mudança de volume atualiza a memória de volume."""
    handler.parse_and_apply("VOLUME:75")
    assert app_state.metadata.volume == 75
    assert app_state.last_non_zero_volume == 75

def test_parse_state_playing(handler, app_state):
    """Verifica se o estado de reprodução é convertido para texto amigável."""
    handler.parse_and_apply("STATE:1")
    assert app_state.metadata.status == "Tocando"
    
    handler.parse_and_apply("STATE:0")
    assert app_state.metadata.status == "Pausado"

def test_calculate_progress(handler, app_state):
    """Verifica se o progresso é calculado proporcionalmente."""
    handler.parse_and_apply("DURATION:04:00")
    handler.parse_and_apply("POSITION:01:00")
    # 60s / 240s = 0.25
    assert app_state.metadata.progress == 0.25

def test_malformed_message(handler):
    """Verifica se mensagens sem o separador ':' são ignoradas com segurança."""
    result = handler.parse_and_apply("INVALID_MESSAGE")
    assert result is None
