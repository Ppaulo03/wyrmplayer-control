---
name: add-config-field
description: Checklist guiado para adicionar um novo campo de configuração ao WyrmPlayerControl (AppConfig). Use quando o usuário pedir para adicionar uma nova opção configurável (ex. novo parâmetro no settings.json, nova opção na tela de configurações).
---

# Add Config Field

Adicionar um campo novo a `AppConfig` (`src/core/config.py`) toca várias partes do app porque a configuração é lida em múltiplos lugares (UI de settings, hot-reload, README). Pular um passo aqui é a forma mais comum de gerar um campo "morto" que salva mas nunca é aplicado, ou que quebra o hot-reload.

## Passos

1. **Schema** — adicione o campo em `AppConfig` (`src/core/config.py`) com um valor default sensato. Campos `dict`/`list` precisam de `field(default_factory=...)`, não valor literal mutável.

2. **UI de settings** — exponha o campo em uma das abas de `src/ui/components/settings/` (`general_tab.py`, `hotkeys_tab.py` ou `layout_tab.py`, conforme a natureza do campo). Siga o padrão existente: o controle Flet é guardado no dicionário `.data` do componente, e `src/ui/settings.py::save_settings` lê esse dicionário para montar o novo `AppConfig` e persistir via `config_manager.save`.

3. **Hot-reload (`ConfigWatcher`)** — decida se o campo precisa de reação em runtime quando o `settings.json` é editado externamente (fora do app):
   - Se sim: adicione o rastreio do valor anterior (`self.last_<campo>`) no `__init__` de `src/services/config_watcher.py` e a comparação/ação em `_check_config_file`, seguindo o padrão já usado para `hotkeys` e `hud_monitor/hud_position`.
   - Se não (campo só lido no próximo restart, ou já coberto por `cfg_manager.load()` sempre que consultado): documente essa decisão no PR/commit, não é obrigatório mexer no watcher.

4. **Consumidores** — verifique quem deveria ler o novo campo (`PlayerController`, `MusicHUD`, `HotkeyManager`, etc. via `self.config.load()`) e conecte a lógica real. Um campo que só existe no schema e na UI, sem nenhum consumidor, não faz nada.

5. **Documentação** — atualize o exemplo de `settings.json` e a lista de "Campos principais" no [README.md](../../../README.md), e o `settings.json` de exemplo na raiz do repo se fizer sentido como default de desenvolvimento.

6. **Testes** — se o campo tem lógica não trivial (validação, cálculo derivado), adicione um teste em `tests/test_config.py` ou no módulo consumidor correspondente.

## Verificação final

Rode a skill `verify` (ruff + mypy + pytest) e, se possível, `uv run python src/main.py` para confirmar manualmente que o campo aparece na tela de configurações, salva corretamente em `settings.json`, e é aplicado (ou recarregado) como esperado.
