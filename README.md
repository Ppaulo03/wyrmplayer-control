# WyrmPlayerControl

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.13%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows&logoColor=white)
![UI](https://img.shields.io/badge/UI-Flet-0095D5)
![Build](https://img.shields.io/badge/Build-PyInstaller-6E4C13)
![Status](https://img.shields.io/badge/Status-Active-success)

Controlador global de mídia para YouTube Music com HUD flutuante, atalhos de teclado do sistema e integração via WebNowPlaying.

O objetivo do projeto é permitir controle de reprodução/volume sem depender do foco do navegador, com uma interface visual discreta que aparece apenas em eventos relevantes.

## Visão Geral

Principais capacidades:

1. Captura metadados e estado de reprodução via WebSocket.
2. Controla play/pause, faixa anterior/próxima, volume e mute com hotkeys globais.
3. Exibe HUD overlay sempre no topo, com auto-hide e posicionamento configurável.
4. Mantém ícone na system tray com ações rápidas.
5. Possui tela de configurações com autosave.
6. Suporta build Windows com PyInstaller.

## Stack e Requisitos

1. Python 3.13+.
2. Gerenciamento de dependências com uv.
3. UI com Flet.
4. Atalhos globais com keyboard.
5. Tray com pystray.
6. Comunicação WebSocket com websockets.

## Estrutura do Projeto

```text
music_controller/
  settings.json
  WyrmPlayerControl.spec
  src/
    main.py
    core/
      config.py
      display.py
      hotkeys.py
      state.py
      websocket.py
    services/
      player_controller.py
    ui/
      hud.py
      settings.py
      tray.py
```

Resumo dos módulos:

1. src/core/config.py: modelo e persistência de configuração.
2. src/core/websocket.py: servidor que recebe eventos da extensão.
3. src/core/hotkeys.py: registro e recarga dos atalhos globais.
4. src/services/player_controller.py: comandos de controle de mídia.
5. src/ui/hud.py: overlay visual responsivo e temporário.
6. src/ui/settings.py: janela de configurações com autosave.
7. src/ui/tray.py: menu da system tray e ações de ciclo de vida.
8. src/main.py: orquestra inicialização, singleton, logging e execução.

## Configuração do Navegador

Para integração com YouTube Music, use a extensão WebNowPlaying Redux no Brave/Chrome.

No Brave, adicione exceção para localhost:

1. Abra brave://settings/shields/filters.
2. Em Filtros Personalizados, adicione:

```text
@@||localhost
```

3. Salve e reinicie o navegador.

## Integração com Spotify (Spicetify)

O WyrmPlayerControl também funciona com o cliente desktop do Spotify, via [Spicetify](https://spicetify.app) e a extensão `webnowplaying.js` (que já vem embutida na instalação do Spicetify).

Pré-requisitos:

1. [Spicetify](https://spicetify.app) instalado.
2. `websocket_port` em `settings.json` configurado como `8974` (padrão) — a extensão do Spicetify conecta nessa porta fixa e não permite alterá-la.

Passos:

1. Ative "Integração com Spotify (Spicetify)" na aba **Geral** das configurações (ou defina `"spotify_integration": true` no `settings.json`).
2. Reinicie o WyrmPlayerControl. Se o Spicetify já estiver instalado, a extensão é registrada automaticamente (sem reiniciar o Spotify).
3. Abra o menu da system tray e clique em **Configurar Spotify**. Isso confirma com você antes de rodar `spicetify apply`, que reinicia o cliente do Spotify para aplicar a integração.

Se o Spicetify não estiver instalado, o app não o instala sozinho — a mensagem no log (e no diálogo da tray) aponta para a instalação manual em https://spicetify.app.

## Arquivo de Configuração

O arquivo settings.json é criado automaticamente se não existir.

Campos principais:

1. volume_step: passo de ajuste de volume.
2. hud_display_time: duração do HUD em segundos.
3. websocket_port: porta do servidor WebSocket local (padrão 8974 — não mude se for usar a integração com Spotify, veja abaixo).
4. hud_monitor: índice do monitor onde o HUD aparece.
5. hud_position: preset de posição do HUD.
6. log_level: nível de logging (DEBUG/INFO/WARNING/ERROR/CRITICAL).
7. log_file: nome ou caminho do arquivo de log.
8. hotkeys: mapa de comandos para atalhos.
9. triggers: quando o HUD deve aparecer (volume/metadata/playback).
10. spotify_integration: habilita a checagem/configuração automática da integração com Spotify via Spicetify (veja abaixo).

Exemplo:

```json
{
  "volume_step": 5,
  "hud_display_time": 3,
  "websocket_port": 8974,
  "hud_monitor": 0,
  "hud_position": "bottom_right",
  "log_level": "INFO",
  "log_file": "wyrmplayer.log",
  "hotkeys": {
    "play_pause": "alt gr+p",
    "next_track": "alt gr+right",
    "previous_track": "alt gr+left",
    "volume_up": "alt gr+up",
    "volume_down": "alt gr+down",
    "mute": "alt gr+m"
  },
  "triggers": {
    "volume": true,
    "metadata": true,
    "playback": true
  },
  "spotify_integration": false
}
```

## Instalação e Execução (Dev)

1. Instale dependências:

```bash
uv sync
```

2. Rode o app:

```bash
uv run python src/main.py
```

3. Rode apenas a tela de configurações (opcional):

```bash
uv run python -m src.ui.settings
```

## Build Windows

Use o spec oficial do projeto:

```bash
uv run pyinstaller WyrmPlayerControl.spec
```

Saída esperada:

1. Executável em dist/WyrmPlayerControl.exe.
2. Artefatos de build em build/.

## Comportamento de Execução

1. Single instance no processo principal para evitar múltiplas cópias concorrentes.
2. Janela de configurações abre em modo dedicado (--settings) sem conflitar com o singleton.
3. Logging dinâmico: mudanças de log_level e log_file são aplicadas em runtime.
4. Encerramento via tray com limpeza de tarefas e finalização do processo.

## Troubleshooting

1. HUD não aparece:
   Verifique conexão da extensão e triggers no settings.
2. Atalhos não funcionam:
   Teste executar o app como administrador.
3. WebSocket sem dados:
   Confirme extensão ativa e exceção localhost no navegador.
4. Build funciona, mas configurações não persistem:
   Verifique o settings.json no diretório ao lado do .exe.

## Licença

Este projeto está licenciado sob a licença MIT.

Consulte o arquivo LICENSE para o texto completo.