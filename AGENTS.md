# AGENTS.md

Guia para agentes (Claude Code e similares) trabalhando neste repositório. Leia isto antes de propor mudanças estruturais.

## O que é o projeto

WyrmPlayerControl é um controlador global de mídia (protocolo WebNowPlaying) para YouTube Music e Spotify, rodando como app desktop Windows (Flet). Ele:

- Recebe metadados/estado de reprodução via WebSocket local (extensão WebNowPlaying Redux no navegador, ou `webnowplaying.js` do Spicetify para Spotify → app).
- Expõe atalhos de teclado globais (play/pause, next/previous, volume, mute), incluindo suporte a jogos fullscreen.
- Mostra um HUD overlay flutuante, transparente, always-on-top, que aparece brevemente em eventos relevantes.
- Vive na system tray, com menu de configurações/recarregar atalhos/sair.
- Persiste configuração em `settings.json` ao lado do executável/raiz do projeto, com hot-reload (watcher de mtime).

Ver [README.md](README.md) para detalhes de uso, instalação e build. Este arquivo foca em como o código está organizado e como trabalhar nele com segurança.

## Stack

- Python 3.13+, gerenciado com `uv` (não use pip diretamente).
- UI: Flet (`ft.run`), duas páginas: HUD (`app_main`) e janela de settings (`--settings`).
- Atalhos globais: lib `keyboard` (fullscreen-safe) com fallback nativo via WinAPI `RegisterHotKey`.
- Tray: `pystray` + `Pillow`.
- WebSocket: lib `websockets` (protocolo de texto simples `CHAVE:VALOR`, não JSON).
- Lint/format: `ruff`. Type-check: `mypy --strict`. Testes: `pytest` (+ `pytest-asyncio`, modo auto).
- Build Windows: PyInstaller via `WyrmPlayerControl.spec`.

## Arquitetura (camadas)

```
src/
  core/            infraestrutura de aplicação: config, estado, hotkeys, websocket, logging, singleton, autostart
    utils/         helpers puros (ex.: parsing de shortcuts de teclado)
  domain/          lógica de domínio pura, sem I/O: parsing de metadata, protocolos (interfaces)
  services/        orquestração: PlayerController (comandos), ConfigWatcher (hot-reload), spotify_setup (diagnóstico/config do Spicetify)
  infrastructure/  chamadas Win32 diretas (ctypes): monitores, janelas, hooks de teclado, elevação
  ui/              Flet: HUD, tray, tela de settings (theme.py + components/settings/*)
  main.py          composition root: cria managers, liga callbacks, roda o loop Flet
```

Fluxo de dados típico (ex.: extensão manda `VOLUME:80`):

1. `MusicWebSocketServer.handler` recebe a mensagem → `_parse_message`.
2. `MetadataHandler.parse_and_apply` (domain, puro) atualiza `AppState.metadata` e devolve `(log_meta, category)`.
3. Servidor agenda `AppState.notify(major, category)` na loop asyncio.
4. `MusicHUD.update_ui` (registrado via `state.on_update`) atualiza a UI e decide se mostra o HUD, respeitando `triggers` da config.

Fluxo de comando (ex.: hotkey de volume):

1. `HotkeyManager` detecta a combinação (via `keyboard` ou hook nativo) e chama `PlayerController.adjust_volume`.
2. `PlayerController` calcula o novo volume e chama `messenger.enqueue_command(...)` — `messenger` é `IMessenger` (protocolo em `domain/protocols.py`), implementado por `MusicWebSocketServer`.
3. Servidor broadcasta o comando de texto para os clientes WebSocket (extensão do navegador).

Pontos de desacoplamento a preservar:

- `PlayerController` não conhece `MusicWebSocketServer` diretamente, só `IMessenger`. Ao trocar de transporte, implemente o protocolo — não importe o server em `services/`.
- `MetadataHandler` é lógica pura (sem asyncio, sem logging de UI) — mantenha assim para facilitar testes unitários.
- `core/display.py` e `infrastructure/win32.py` separam abstração (cálculo de posição/tamanho do HUD) de chamadas Win32 cruas.

## Convenções específicas deste repo

- **Símbolo da marca é "Cue"** (`scripts/generate_icons.py`): coluna vertical + seta de play recortada, construído só com linhas retas (sem curva orgânica), seguindo a régua de iconografia do sistema "wyrm" — nunca volte a uma ilustração literal de dragão/criatura. Gera `assets/icon.ico`, `icon2.ico`, `tray.ico` (multi-resolução), `original.png` e `cue-mark.png` a partir das mesmas coordenadas; rode o script de novo (`uv run python scripts/generate_icons.py`) em vez de editar os `.ico` na mão se precisar ajustar cor/proporção.
- **Tela de configurações usa o sistema visual "wyrm"** (`src/ui/theme.py`): void/plate por luminância (sem sombra), hairline de 1px, zero `border_radius`, um único accent (`#D99A3A`). Tipografia em 3 fontes carregadas de `assets/fonts/*.ttf` via `page.fonts` (Archivo para títulos, IBM Plex Sans pro corpo, JetBrains Mono para todo dado — porta, %, segundos, status). Novo controle nesse padrão? Use os helpers de `theme.py` (`wyrm_switch`, `row`, `row_text`, `group_label`, `mono`, `value_text`) em vez de `ft.Switch`/cores soltas — mantém a tela inteira consistente.
- **`theme.wyrm_switch` não é um `ft.Switch`**: é um `ft.Container` customizado (sem cantos arredondados) que guarda seu próprio estado em `.data` (bool), não em `.value`. Ao ler esses campos em `save_settings()`, use `.data`, não `.value` — só sliders/dropdowns/textfields nativos do Flet usam `.value`.
- **Navegação da tela de settings é por seção** (`src/ui/settings.py`): 5 seções (Geral/Atalhos/Exibição/Integrações/Avançado) numa barra lateral, cada uma num arquivo próprio em `components/settings/`. Adicionar uma opção nova? Decida primeiro em qual seção ela pertence pelo tema, não pelo arquivo que já existe.
- **Comentários e logs em português**; mantenha esse idioma ao editar código existente. Novo código pode seguir o mesmo padrão para consistência.
- **Sem type hints ausentes**: `mypy --strict` está configurado; funções novas precisam de assinatura tipada completa.
- **`AppConfig` é a fonte da verdade de schema de config** (`src/core/config.py`). Adicionar um campo novo requer: (1) campo no dataclass com default, (2) leitura/escrita na respectiva aba em `src/ui/components/settings/`, (3) tratamento em `ConfigWatcher` se precisar reagir a mudanças em runtime, (4) atualizar exemplo no `README.md`.
- **Hotkeys têm dois backends**: biblioteca `keyboard` (preferencial, funciona em fullscreen) e fallback nativo via `RegisterHotKey`/hook de baixo nível em `infrastructure/win32.py`. Ao mexer em `HotkeyManager`, teste os dois caminhos ou pelo menos entenda qual está sendo exercitado.
- **Protocolo WebSocket é texto simples**, não JSON: mensagens `CHAVE:VALOR` (ex.: `TITLE:...`, `VOLUME:80`, `STATE:1`). Comandos enviados ao cliente são strings livres (`playPause`, `next`, `previous`, `setVolume <n>`). Não introduza JSON sem atualizar também a extensão do navegador (fora deste repo).
- **`src/main.py` é o composition root**: evite lógica de negócio nova ali; adicione em `services/` ou `core/` e apenas conecte callbacks em `src/main.py`.
- **Single instance via mutex global do Windows** (`core/single_instance.py`), com fallback de lockfile. A janela de settings roda em processo separado (`--settings`) e não é bloqueada pelo singleton.
- **Estado compartilhado (`AppState`) usa observer assíncrono** (`on_update`/`notify`), não signals/eventos do Flet. Novas partes da UI que precisam reagir a mudanças devem se registrar via `state.on_update`.
- **`websocket_port` default é `8974`**, não mude sem necessidade forte: é a porta fixa (não configurável) esperada tanto pela extensão de navegador quanto pela extensão `webnowplaying.js` do Spicetify.
- **Callbacks de menu do `pystray` rodam na mesma thread que bombeia as mensagens da tray** (confirmado no código-fonte do pystray: `Icon._handler` executa o callback sincronamente dentro do loop `GetMessage`/`DispatchMessage`). Qualquer callback que faça algo bloqueante (subprocess, diálogo nativo) precisa rodar numa thread própria (ver `SystemTrayManager._configure_spotify`), senão a tray inteira trava até terminar — foi exatamente o bug que causou "o prompt de confirmação trava na tela" na integração com Spotify.
- **`core/autostart.py` escreve no registro real do Windows** (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`). `sync(enabled, elevated=...)` é idempotente (compara o comando registrado com `get_startup_command(elevated)` e só reescreve se divergir) — prefira sempre `sync()` a chamar `enable()`/`disable()` diretamente. `elevated=True` omite `--no-admin-relaunch`, fazendo o app pedir UAC a cada login em troca de atalhos confiáveis sobre janelas/jogos elevados. Em testes, sempre faça monkeypatch de `autostart.winreg.*`/`autostart.enable`/`autostart.disable`/`autostart.get_registered_command` — nunca deixe um teste tocar o registro de verdade.
- **Integração com Spotify (`services/spotify_setup.py`) só faz auto-config segura**: no startup, se `spotify_integration` estiver true, só roda `spicetify config extensions` (não reinicia o Spotify). Rodar `spicetify apply` (que reinicia o cliente) exige clique explícito no item "Configurar Spotify" da tray + confirmação via `win32.confirm_dialog`. Não automatize esse último passo sem repensar — é uma ação com efeito colateral visível pro usuário (fecha/reabre o Spotify).
- **Spotify via Microsoft Store não é compatível com Spicetify** (app em sandbox, não pode ser modificado). `spotify_setup.is_spotify_microsoft_store()` detecta isso checando `%LOCALAPPDATA%\Packages\SpotifyAB.SpotifyMusic_*` — sem essa checagem, `spicetify config`/`apply` falham silenciosamente (retornam código != 0) sem explicar o motivo real ao usuário.
- **Spicetify se recusa a rodar enquanto elevado** (por padrão), e `src/main.py` roda o WyrmPlayerControl inteiro como admin — logo qualquer subprocess `spicetify` herda essa elevação e falha. `win32.run_command_unelevated()` roda o comando de verdade sem privilégios administrativos, via uma tarefa agendada temporária (`-LogonType S4U -RunLevel Limited`, criada/rodada/limpa via PowerShell). Verificado manualmente (duas técnicas: `New-ScheduledTaskPrincipal` e `schtasks /rl limited` direto): o processo filho roda em "Nível Obrigatório Médio" de verdade, mesmo com o pai elevado — **mas** o Spicetify ainda detecta o token criado por logon S4U como "elevado" (provavelmente checa `TokenElevationType` em vez do nível de integridade real) e se recusa a rodar mesmo assim. Por isso `_run_spicetify` também acrescenta `--bypass-admin` quando `avoid_admin=True` — só é seguro nesse caso específico porque a execução já é genuinamente não-elevada (confirmado); nunca combine `--bypass-admin` com uma chamada realmente elevada.

## Comandos úteis

```bash
uv sync
```

```bash
uv run python src/main.py
```

```bash
uv run python -m src.ui.settings
```

```bash
uv run pytest
```

```bash
uv run ruff check .
```

```bash
uv run ruff format .
```

```bash
uv run mypy src
```

```bash
uv run pyinstaller WyrmPlayerControl.spec
```

## Testes

- `tests/` cobre: `config` (persistência/defaults), `state` (observer), `metadata_handler` (parsing de protocolo), `player_controller` (comandos/volume/mute), `keyboard_utils` (expansão de shortcuts).
- Fixtures em `tests/conftest.py`: `temp_config_file`, `config_manager` (isolado em arquivo temporário — nunca sobrescreve o `settings.json` real), `app_state`.
- `PlayerController` é fácil de testar isoladamente: injete um fake `IMessenger` (só precisa de `enqueue_command`).
- Não há testes de UI (Flet) nem dos hooks Win32 — mudanças em `ui/` e `infrastructure/win32.py` exigem verificação manual no Windows (rodar `uv run python src/main.py`).
- Testes que dependem de comportamento específico do Windows (`ctypes.windll`, `RegisterHotKey`) só rodam/fazem sentido em ambiente Windows.

## Skills úteis neste projeto

Este é um app desktop Windows (Flet + ctypes/Win32), não um projeto web — a maior parte das skills voltadas a artifacts/web/dataviz não se aplica ao dia a dia aqui.

Skills de projeto (`.claude/skills/`), criadas especificamente para este repo:

- **`verify`**: roda o gate de qualidade completo (`ruff check`, `ruff format --check`, `mypy --strict`, `pytest`) em sequência. Use antes de considerar qualquer mudança pronta.
- **`add-config-field`**: checklist guiado para adicionar um novo campo a `AppConfig` sem esquecer nenhuma das partes que precisam mudar junto (schema, aba de settings, `ConfigWatcher`, consumidores, README, testes).

Skills genéricas relevantes:

- **`code-review`**: rodar antes de qualquer PR, principalmente em mudanças em `core/hotkeys.py`, `infrastructure/win32.py` e `services/config_watcher.py` — são áreas com estado mutável, threads e chamadas ctypes onde bugs sutis (leaks de handle, race condition, hotkey não liberada) são fáceis de introduzir e difíceis de notar em teste manual rápido.
- **`security-review`**: relevante porque o projeto usa `ctypes.windll` diretamente (hooks de teclado de baixo nível, `RegisterHotKey`, manipulação de janelas), abre um servidor WebSocket local sem autenticação, e relança o processo como admin (`relaunch_as_admin_if_needed`). Rodar após mudanças nessas áreas, especialmente em `win32.py` e `websocket.py`.
- **`run`**: útil para subir o app (`uv run python src/main.py`) e confirmar visualmente uma mudança de UI (HUD/settings) — mas como é uma janela desktop nativa (Flet), não uma página web, o agente não consegue *ver* a janela via browser; ele serve para iniciar o processo e checar logs/erros, não para inspecionar visualmente o HUD. Verificação visual do HUD/tray continua sendo manual.
- **`init`**: já não é necessário aqui — este AGENTS.md cumpre esse papel; não crie um CLAUDE.md duplicado.
- **`simplify`**: útil pontualmente em `hotkeys.py` (lógica de dois backends) e `win32.py`, que acumulam bastante código de baixo nível — mas use com cautela: parte da "complexidade" ali é inerente ao domínio (Win32/ctypes), não code smell.
- **`fewer-permission-prompts`**: rode depois de algumas sessões reais de desenvolvimento para complementar o allowlist manual já configurado em `.claude/settings.json`, caso surjam outros comandos repetitivos (ex.: `uv run pyinstaller`).

Não são relevantes para este projeto: `dataviz`, `design`, `artifact-*`, `docx`/`pptx`/`xlsx` (não há geração de documentos/planilhas aqui).

## Cuidados ao alterar

- **Não remova o `os._exit(0)` final em `src/main.py`** sem entender por quê está lá: threads não-daemon (pystray, hook nativo) podem impedir o encerramento normal do processo.
- **Mudanças em `HotkeyManager.setup`** afetam dois backends; teste recarregamento de hotkeys (tray → "Recarregar Atalhos") e o hot-reload automático via `ConfigWatcher`.
- **`ConfigWatcher` faz debounce de lock/unlock de sessão** (3 amostras estáveis, cooldown de 8s) para evitar re-registrar hotkeys em falsos positivos — não simplifique sem entender essa lógica.
- **HUD usa truque de posicionamento fora da tela** (`window.left = -32000`) para evitar "flash" central antes de reposicionar — preserve essa sequência ao mexer em `show_hud`/`apply_layout`.
- Este projeto roda apenas em **Windows**; funções em `infrastructure/win32.py` e partes de `core/` têm fallbacks (`if os_name() != "nt"`) só para não quebrar em CI/dev cross-platform, não são um alvo de suporte real.
