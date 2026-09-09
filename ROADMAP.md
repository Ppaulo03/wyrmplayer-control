# Roadmap

Plano de trabalho para as próximas expansões do WyrmPlayerControl, organizado por fase. Cada fase é razoavelmente independente das seguintes, mas a ordem importa onde há dependência técnica (ver notas).

## Fase 0 — Corrigir bugs já identificados

Debt pequeno e independente, sem dependências entre si. Fazer primeiro por serem rápidos e não bloquearem nada.

1. **Hotkey de mute (`alt gr+,`) nunca registra.** A lib `keyboard` falha ao normalizar o nome da tecla `,` nas 3 variantes tentadas (`alt gr+,`, `ctrl+alt+,`, `right alt+,`). Já existe uma tarefa em background sinalizada para isso (`task_adc04d1c`).
2. **`ConfigWatcher.on_websocket_port_change` nunca é chamado.** O callback está todo cabeado (`main.py` passa `restart_websocket_server`, `ConfigWatcher` guarda `last_websocket_port`), mas `_check_config_file` nunca compara `cfg.websocket_port != self.last_websocket_port` nem invoca o callback — mudar a porta pelo `settings.json` em runtime não reinicia o servidor WebSocket. Achado durante a sessão de trabalho na integração com Spotify, ainda não corrigido.

## Fase 1 — Iniciar com o Windows

Feature isolada e rápida, sem dependência de UI nova.

- Novo campo `AppConfig.start_with_windows: bool`.
- Toggle na aba **Geral** das configurações.
- Implementação via `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (não precisa admin) — adiciona/remove uma entrada apontando pro executável (ou `python.exe src/main.py` em modo dev) quando o toggle muda.
- Cuidado: em build (PyInstaller), o caminho registrado deve ser o `.exe` final, não o script Python.

## Fase 2 — Rework visual do HUD

Fundação para a Fase 3 — as features interativas (barra de progresso clicável, HUD seguindo o monitor do mouse) devem ser construídas sobre o HUD já reformulado, não sobre o atual, pra evitar retrabalho.

1. **Conceitos visuais**: eu proponho 2-3 mockups/direções de estilo (ex.: minimalista vs. mais denso em informação, paletas diferentes) antes de tocar em código, para alinhar com você.
2. **Implementação** da direção escolhida em `src/ui/hud.py`, preservando os comportamentos funcionais que já existem e não podem regredir:
   - Truque de posicionamento fora da tela (`window.left = -32000`) para evitar flash central antes de reposicionar.
   - `apply_window_stealth`/`force_topmost` (remoção de decorações, always-on-top agressivo).
   - Redimensionamento responsivo por monitor (`_calculate_window_size`).
   - Fade in/out (`_hide_after_delay`).
   - Os triggers configuráveis (volume/metadata/playback) continuam controlando quando o HUD aparece.

## Fase 3 — Funcionalidades interativas do HUD

Depende da Fase 2 (construir sobre o HUD novo).

1. **Clicar na barra de progresso para buscar posição.** O protocolo já suporta `SETPOSITION`/`SETPROGRESS` do lado da extensão; falta capturar o clique na `ProgressBar` do Flet (ou trocar por um `GestureDetector` sobre ela) e traduzir a posição X do clique em porcentagem, enviando o comando via `PlayerController`/`IMessenger`.
2. **HUD segue o monitor onde está o mouse.** Hoje `hud_monitor` é fixo na config. Precisa: detectar a posição do cursor (`GetCursorPos` via ctypes, em `infrastructure/win32.py`), mapear pra qual `MonitorArea` contém esse ponto (`core/display.py`), e decidir se isso substitui `hud_monitor` ou vira uma opção adicional (`hud_monitor: "auto"`).
3. **Suprimir o HUD durante jogos em fullscreen.** Precisa detectar se a janela em primeiro plano está em modo fullscreen exclusivo (heurística comum: comparar o retângulo da janela ativa com o retângulo do monitor inteiro, ver se não é a própria shell do Windows). Conecta com o suporte a fullscreen que já existe nos hotkeys (`_setup_low_level_keyboard_hotkeys` com `suppress=False`).

## Fase 4 — Scrobbling para o Last.fm

**Bloqueado**: precisa de uma API key do Last.fm (gratuita, mas você precisa criar em https://www.last.fm/api/account/create com sua conta) antes de eu poder implementar e testar de verdade.

Quando desbloqueado:
- Novo serviço `src/services/lastfm_scrobbler.py`.
- Fluxo de autenticação do Last.fm (obter session key via API — normalmente um fluxo de autorização no navegador uma vez, depois a session key fica salva localmente).
- Enviar "now playing" + "scrobble" (após ~50% da música ou 4 minutos, o que vier primeiro, conforme regras do Last.fm) a partir dos eventos de metadata que já chegam via `MetadataHandler`.
- Novo campo de config: `lastfm_enabled`, e a session key salva separadamente de `settings.json` (dado sensível — considerar não versionar/expor em texto puro, ou pelo menos não incluir no exemplo do README).

## Ordem sugerida de execução

1. Fase 0 (bugs) — rápido, sem risco, libera espaço mental.
2. Fase 1 (iniciar com Windows) — rápido, independente.
3. Fase 2 (mockups do HUD) — eu proponho, você escolhe.
4. Fase 2 (implementação do HUD escolhido).
5. Fase 3 (features interativas sobre o HUD novo).
6. Fase 4 (Last.fm) — quando a API key estiver disponível, pode entrar em paralelo a qualquer momento acima, já que é isolada.
