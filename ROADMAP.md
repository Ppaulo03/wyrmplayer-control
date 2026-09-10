# Roadmap

Plano de trabalho para as próximas expansões do WyrmPlayerControl, organizado por fase. Cada fase é razoavelmente independente das seguintes, mas a ordem importa onde há dependência técnica (ver notas).

## Fase 0 — Corrigir bugs já identificados ✅ concluída

1. ✅ **Hotkey de mute (`alt gr+,`) nunca registrava.** Corrigido traduzindo `,` para o nome canônico `comma` (a lib `keyboard` reserva vírgula como separador de múltiplos passos) e adicionando `VK_OEM_COMMA` ao fallback nativo. Testes de regressão em `test_keyboard_utils.py`.
2. ✅ **Hot-reload de `websocket_port`, `log_level` e `log_file` não fazia nada.** `ConfigWatcher._check_config_file` rastreava esses valores mas nunca comparava/chamava os callbacks. Corrigido — agora `on_websocket_port_change` e `apply_logging_configuration` são invocados de verdade. Cobertura nova em `tests/test_config_watcher.py`.

## Fase 1 — Iniciar com o Windows ✅ concluída

- `AppConfig.start_with_windows: bool` + toggle na aba **Geral**.
- `src/core/autostart.py` gerencia `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (não precisa admin), com `sync()` idempotente chamado ao salvar nas configurações e no startup do `main.py`.
- Usa `pythonw.exe` em modo dev (sem console) e o próprio executável em build (PyInstaller).
- Decisão consciente: por padrão o comando registrado usa `--no-admin-relaunch` pra não pedir UAC a cada login — trade-off documentado no README (hotkeys podem não funcionar sobre janelas elevadas até reabrir manualmente).
- Opção secundária `start_with_windows_elevated` (só visível/relevante com `start_with_windows` ativo): quem precisa de atalhos garantidos sobre jogos elevados pode aceitar o prompt de UAC a cada login em troca disso. Avaliamos usar Tarefa Agendada com `-RunLevel Highest` para eliminar o prompt por completo, mas descartamos por ser mais complexo e por Tarefas Agendadas com elevação automática no login serem um padrão que alguns antivírus/EDR tratam como suspeito (técnica clássica de bypass de UAC).
- Testado de ponta a ponta contra o registro real (enable → is_enabled → disable, sem deixar resíduo).

## Fase 1.5 — Redesign da tela de configurações ✅ concluída

Fora da ordem original do roadmap — o usuário pediu pra priorizar isso antes do HUD.

- Reorganização: as 3 abas no topo (Geral/Atalhos/Exibição) viraram 5 seções numa barra lateral (Geral/Atalhos/Exibição/**Integrações**/**Avançado**) — Spotify e log/porta ganharam seção própria, tirando peso da aba Geral.
- Visual: novo sistema "wyrm" (`src/ui/theme.py`) baseado no documento de identidade do usuário — void/plate por luminância (sem sombra/gradiente), zero `border_radius`, hairline de 1px, accent único `#D99A3A`, 3 fontes bundladas em `assets/fonts/` (Archivo, IBM Plex Sans, JetBrains Mono).
- Componentes customizados: `theme.wyrm_switch` (switch quadrado, guarda estado em `.data`) e `theme.wyrm_slider` (Slider nativo do Flet, tematizado). Dropdowns/TextFields mantidos nativos, só restilizados.
- Processo: 2 rounds de mockup (HTML) antes de implementar — o primeiro concept agradou na organização mas "parecia muito IA" (serifa decorativa, sombra, gradiente); o segundo aplicou o documento de identidade "wyrm" do usuário à risca.
- Escopo consciente: a barra de título da janela continua a padrão do Windows (não foi pra frameless) — só o conteúdo interno segue o sistema novo.

## Fase 2 — Rework visual do HUD

**Pausada** (o usuário pediu pra tratar o redesign das configurações primeiro — ver Fase 1.5). Os 3 conceitos visuais (Signal/Console/Aperture) já foram mostrados; falta o usuário escolher uma direção antes de implementar. Provavelmente vale reaplicar o sistema "wyrm" (agora estabelecido na Fase 1.5) em vez de uma das 3 propostas originais, já que elas antecederam essa decisão de identidade.

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
