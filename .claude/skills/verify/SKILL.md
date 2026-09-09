---
name: verify
description: Roda o gate de qualidade completo do WyrmPlayerControl (ruff check, ruff format --check, mypy --strict, pytest) e reporta falhas de forma consolidada. Use antes de considerar uma mudança pronta, antes de commit/PR, ou quando o usuário pedir para "verificar", "rodar os checks" ou "validar" o código.
---

# Verify

Gate de qualidade deste projeto (Python 3.13 + uv). Rode os quatro checks abaixo, nesta ordem, parando cedo apenas se um comando falhar de forma que invalide os seguintes (ex.: erro de sintaxe).

## Passos

1. Lint:
```bash
uv run ruff check .
```

2. Formatação (checagem, não aplica automaticamente):
```bash
uv run ruff format --check .
```

3. Type-check (`mypy --strict`, configurado em `pyproject.toml`):
```bash
uv run mypy src
```

4. Testes:
```bash
uv run pytest
```

## Ao encontrar falhas

- **ruff check**: corrija os problemas reportados diretamente no código; não desabilite regras via `# noqa` sem justificativa forte.
- **ruff format --check**: rode `uv run ruff format .` para aplicar a formatação automaticamente, depois re-verifique.
- **mypy**: não use `# type: ignore` como atalho — só quando genuinamente inevitável (ex.: limitação de biblioteca sem stubs, como já ocorre pontualmente com `flet`/`pystray`). Prefira corrigir a tipagem.
- **pytest**: leia a falha, identifique se é regressão introduzida pela mudança atual ou teste desatualizado. Não pule testes com `-k` para "fazer passar" — resolva a causa raiz.

## Observações do projeto

- Testes de UI (Flet) e dos hooks Win32 (`infrastructure/win32.py`, backend nativo de `hotkeys.py`) não são cobertos por `pytest` — funcionam apenas em Windows e exigem verificação manual (`uv run python src/main.py`).
- Depois de rodar tudo com sucesso, reporte um resumo curto (o que passou, o que foi corrigido) — não é necessário colar a saída completa dos comandos.
