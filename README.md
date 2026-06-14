# KanBots Bridge - Viaxis

Repo ponte entre Hermes (orquestrador) e KanBots (executor).

## Como funciona

1. **Hermes** (VPS) cria issues com tarefas detalhadas
2. **Claude** (via GitHub) revisa e aprova issues críticas
3. **KanBots** (Windows) lê issues aprovadas e executa
4. **Hermes** verifica PRs/commits e fecha issues concluídas

## Estrutura
- Issues = tarefas
- Labels = prioridade e área
- Milestones = entregas semanais
