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
Deve responder "Nenhuma issue com kanbots:ready" (se não houver issues pendentes).

## Uso

### Modo single-run (recomendado para teste)
```powershell
python kanbots/monitor.py --once
```
Processa 1 issue com `kanbots:ready` e sai.

### Modo watch (contínuo)
```powershell
python kanbots/monitor.py --watch
```
Fica rodando em loop, verificando a cada 60s. Use Ctrl+C para parar.

### Como serviço do Windows (opcional)
Para rodar em background após reboot:
```powershell
# Usando NSSM (Non-Sucking Service Manager)
winget install nssm
nssm install Kanbots "C:\Program Files\Python312\python.exe" "C:\Users\Claudio\kanbots\bridge\kanbots\monitor.py --watch"
nssm set Kanbots AppDirectory "C:\Users\Claudio\kanbots\bridge"
nssm start Kanbots
```

## Como funciona

```
1. Kanbots monitora viaxis-bridge por issues com label kanbots:ready
2. Encontrou → aplica kanbots:in-progress
3. Lê a issue → extrai repositório alvo (ex: claudio0507/ansvorc)
4. Clona/atualiza o repo no workspace
5. Cria branch feat/issue-N-descricao
6. Invoca Claude Code com a tarefa
7. Commit + push + abre PR
8. Aplica kanbots:done + comenta resultado na issue
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

### Permission denied ao clonar
Verifique se o token do gh tem acesso aos repositórios:
```powershell
gh auth status
gh auth refresh -h github.com -s repo
```

### Workspace não encontrado
Defina manualmente:
```powershell
$env:KANBOTS_WORKSPACE = "C:\Users\Claudio\kanbots\workspace"
python kanbots/monitor.py --once
```

### Claude Code timeout
Aumente o timeout em `kanbots/config.yaml`:
```yaml
claude:
  timeout: 1200  # 20 minutos
```
