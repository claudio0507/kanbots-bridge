# Configuração KanBots — Guia Completo

## Pré-requisitos (Windows)

Instale antes de começar:

1. **Git** — https://git-scm.com/download/win
2. **GitHub CLI** — `winget install GitHub.cli` e autentique com `gh auth login`
3. **Python 3.10+** — `winget install Python.Python.3.12`
4. **Claude Code** — `npm install -g @anthropic-ai/claude-code` e autentique com `claude login`

Verifique:
```powershell
gh auth status
claude --version
python --version
```

## Instalação

### 1. Clone o bridge
```powershell
mkdir C:\Users\Claudio\kanbots
cd C:\Users\Claudio\kanbots
git clone https://github.com/claudio0507/kanbots-bridge.git bridge
```

### 2. Configure o workspace
```powershell
mkdir C:\Users\Claudio\kanbots\workspace
```

### 3. Ajuste o config (opcional)
Edite `bridge/kanbots/config.yaml` se seu usuário ou caminhos forem diferentes.

### 4. Teste
```powershell
cd C:\Users\Claudio\kanbots\bridge
python kanbots/monitor.py --once
```

## Criando Cards (Issues)

Cada "card" é uma issue no `kanbots-bridge`. Use o template ou crie manualmente.

### Template rápido
```markdown
## Objetivo
Adicionar endpoint de busca por nome

## Contexto
- **Projeto:** claudio0507/ansvorc
- **Modelo:** sonnet

## Critérios de Aceite
- [ ] GET /api/v1/clientes?nome=xxx funciona
- [ ] Testes passam
```

### Campo `modelo:` — como funciona

O Kanbots decide qual modelo usar por ordem de prioridade:

| Prioridade | Fonte | Exemplo |
|-----------|-------|---------|
| **1** | Campo explícito na issue | `model: sonnet` no corpo |
| **2** | Label da issue | issue com label `bug` → usa haiku |
| **3** | Default do config.yaml | `claude.default_model` |

**Presets disponíveis** (definidos em `kanbots/config.yaml`):
- `haiku` → claude-3-5-haiku (rápido, barato — bugs, docs, testes)
- `sonnet` → claude-sonnet-4 (平衡 — features, refactors)
- `opus` → claude-opus-4 (máxima qualidade — tarefas complexas)

**Exemplo de card com modelo explícito:**
```markdown
## Objetivo
Refatorar o módulo de cálculo BDI

## Contexto
- **Projeto:** claudio0507/ansvorc
- **Modelo:** opus
```

### Mapeamento automático por label

Se a issue NÃO tiver `model:`, o Kanbots olha as labels:

| Label da issue | Modelo usado |
|---------------|-------------|
| `bug` | haiku |
| `test` | haiku |
| `docs` | haiku |
| `feature` | sonnet |
| `refactor` | sonnet |

Editável no `config.yaml` → `model_by_label`.

## Uso

### Modo single-run
```powershell
python kanbots/monitor.py --once
```

### Modo watch (contínuo)
```powershell
python kanbots/monitor.py --watch
```

### Como serviço do Windows
```powershell
winget install nssm
nssm install Kanbots "C:\Program Files\Python312\python.exe" "C:\Users\Claudio\kanbots\bridge\kanbots\monitor.py --watch"
nssm set Kanbots AppDirectory "C:\Users\Claudio\kanbots\bridge"
nssm start Kanbots
```

## Labels do ciclo

| Label | Quem aplica | Significado |
|-------|-------------|-------------|
| `hermes:planned` | Hermes | Issue criada, aguardando revisão |
| `aprovado` | Claude (review) | Revisão aprovada |
| `kanbots:ready` | Hermes/Claude | Pronto para execução |
| `kanbots:in-progress` | Kanbots | Em execução |
| `kanbots:done` | Kanbots | Concluído |

## Troubleshooting

### gh: command not found
```powershell
winget install GitHub.cli
gh auth login
```

### claude: command not found
```powershell
npm install -g @anthropic-ai/claude-code
claude login
```

### Claude Code timeout
```yaml
# kanbots/config.yaml
claude:
  timeout: 1200  # 20 minutos
```
