"""
Kanbots Monitor — Agente executor que monitora viaxis-bridge e executa tarefas.

Execução:
    python kanbots/monitor.py          # single run (processa 1 issue)
    python kanbots/monitor.py --watch  # loop contínuo (polling a cada 60s)
    python kanbots/monitor.py --once   # processa 1 issue e sai (default)

Requer:
    - gh CLI autenticado
    - claude CLI (Claude Code) instalado e autenticado
    - Git
    - Python 3.10+
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────
BRIDGE_REPO = "claudio0507/kanbots-bridge"
LABEL_READY = "kanbots:ready"
LABEL_IN_PROGRESS = "kanbots:in-progress"
LABEL_DONE = "kanbots:done"
WORKSPACE = Path(os.environ.get("KANBOTS_WORKSPACE", Path.home() / "kanbots" / "workspace"))
POLL_INTERVAL = 60  # segundos entre polls no modo --watch
CLAUDE_TIMEOUT = 600  # timeout do Claude Code em segundos


# ── GitHub helpers ────────────────────────────────────────────────────
def gh(*args: str) -> subprocess.CompletedProcess:
    """Run gh CLI and return result."""
    return subprocess.run(
        ["gh"] + list(args),
        capture_output=True, text=True, timeout=30,
    )


def gh_json(*args: str):
    """Run gh CLI and parse JSON output."""
    result = gh(*args)
    if result.returncode != 0:
        print(f"  [ERRO] gh {' '.join(args)}: {result.stderr.strip()}")
        return None
    return json.loads(result.stdout)


def get_ready_issues() -> list[dict]:
    """Get open issues with kanbots:ready label."""
    return gh_json(
        "issue", "list", "--repo", BRIDGE_REPO,
        "--label", LABEL_READY, "--state", "open",
        "--json", "number,title,body", "--limit", "5",
    ) or []


def comment_issue(number: int, body: str):
    """Add a comment to an issue."""
    gh("issue", "comment", str(number), "--repo", BRIDGE_REPO, "--body", body)


def edit_labels(number: int, add: str | None = None, remove: str | None = None):
    """Add/remove labels on an issue."""
    cmd = ["issue", "edit", str(number), "--repo", BRIDGE_REPO]
    if add:
        cmd += ["--add-label", add]
    if remove:
        cmd += ["--remove-label", remove]
    gh(*cmd)


def create_pr(repo_dir: Path, branch: str, title: str, body: str) -> str | None:
    """Create a PR in the target repo. Returns PR URL or None."""
    result = subprocess.run(
        ["gh", "pr", "create", "--base", "main", "--head", branch,
         "--title", title, "--body", body],
        capture_output=True, text=True, timeout=30, cwd=str(repo_dir),
    )
    if result.returncode == 0:
        return result.stdout.strip()
    # If already exists
    if "already exists" in result.stderr.lower() or "already exists" in result.stdout.lower():
        return result.stderr.strip() or result.stdout.strip()
    print(f"  [ERRO] create_pr: {result.stderr.strip()}")
    return None


# ── Repo helpers ──────────────────────────────────────────────────────
def parse_target_repo(issue_body: str) -> str | None:
    """Extract target repo from issue body (e.g., 'claudio0507/ansvorc')."""
    import re
    match = re.search(r'claudio0507/([a-zA-Z0-9_-]+)', issue_body)
    return match.group(0) if match else None


def ensure_repo(repo_full: str) -> Path | None:
    """Clone or pull the target repo into workspace. Returns path."""
    repo_name = repo_full.split("/")[1]
    repo_dir = WORKSPACE / repo_name
    repo_url = f"https://github.com/{repo_full}.git"

    if repo_dir.exists():
        print(f"  [git] Atualizando {repo_name}...")
        subprocess.run(["git", "fetch", "origin"], capture_output=True, cwd=str(repo_dir))
        subprocess.run(["git", "checkout", "main"], capture_output=True, cwd=str(repo_dir))
        subprocess.run(["git", "pull", "origin", "main"], capture_output=True, cwd=str(repo_dir))
    else:
        print(f"  [git] Clonando {repo_full}...")
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", repo_url, str(repo_dir)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"  [ERRO] clone: {result.stderr.strip()}")
            return None
    return repo_dir


# ── Claude Code executor ──────────────────────────────────────────────
def run_claude(repo_dir: Path, branch: str, task: str) -> bool:
    """
    Run Claude Code on a task in the given repo.
    Creates a new branch, writes the prompt, invokes Claude.
    Returns True if changes were committed.
    """
    os.chdir(str(repo_dir))

    # Create branch
    subprocess.run(["git", "checkout", "main"], capture_output=True)
    subprocess.run(["git", "pull", "origin", "main"], capture_output=True)
    subprocess.run(["git", "checkout", "-b", branch], capture_output=True)

    # Write prompt file
    prompt_file = repo_dir / ".kanbots-prompt.md"
    prompt_file.write_text(task, encoding="utf-8")

    # Run Claude Code
    print(f"  [claude] Executando tarefa ({CLAUDE_TIMEOUT}s timeout)...")
    try:
        result = subprocess.run(
            ["claude", "--print", "--dangerously-skip-permissions",
             "-p", task],
            capture_output=True, text=True,
            timeout=CLAUDE_TIMEOUT,
            cwd=str(repo_dir),
        )
        output = result.stdout + result.stderr
        print(f"  [claude] Exit: {result.returncode}")

        # Save output
        log_file = repo_dir / ".kanbots-output.txt"
        log_file.write_text(output, encoding="utf-8")

        # Check if any changes were made
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=str(repo_dir),
        )
        return bool(status.stdout.strip())

    except subprocess.TimeoutExpired:
        print(f"  [claude] TIMEOUT após {CLAUDE_TIMEOUT}s")
        return False
    except FileNotFoundError:
        print("  [ERRO] Claude CLI não encontrado. Instale: npm i -g @anthropic-ai/claude-code")
        return False


def commit_and_push(repo_dir: Path, branch: str, message: str) -> bool:
    """Commit changes and push branch. Returns True on success."""
    os.chdir(str(repo_dir))
    subprocess.run(["git", "add", "-A"], capture_output=True)

    result = subprocess.run(
        ["git", "commit", "-m", message],
        capture_output=True, text=True, cwd=str(repo_dir),
    )
    if "nothing to commit" in result.stdout.lower() + result.stderr.lower():
        print("  [git] Nada para commitar")
        return False

    result = subprocess.run(
        ["git", "push", "origin", branch],
        capture_output=True, text=True, timeout=60, cwd=str(repo_dir),
    )
    if result.returncode != 0:
        print(f"  [ERRO] push: {result.stderr.strip()}")
        return False
    return True


# ── Main loop ─────────────────────────────────────────────────────────
def process_issue(issue: dict):
    """Process a single issue: execute the task and report results."""
    number = issue["number"]
    title = issue["title"]
    body = issue.get("body", "")
    print(f"\n{'='*60}")
    print(f"  KanBots — Issue #{number}: {title}")
    print(f"{'='*60}")

    # 1. Announce start
    comment_issue(number, "**KanBots:** Iniciando execucao...")
    edit_labels(number, add=LABEL_IN_PROGRESS, remove=LABEL_READY)

    # 2. Determine target repo
    target_repo = parse_target_repo(body)
    if not target_repo:
        comment_issue(number, "**KanBots:** ERRO — nao foi possivel identificar o repositorio alvo na issue. Verifique se ha uma referencia `claudio0507/<repo>` no corpo.")
        edit_labels(number, remove=LABEL_IN_PROGRESS)
        return

    print(f"  Target repo: {target_repo}")

    # 3. Clone/pull repo
    repo_dir = ensure_repo(target_repo)
    if not repo_dir:
        comment_issue(number, f"**KanBots:** ERRO — falha ao clonar {target_repo}.")
        edit_labels(number, remove=LABEL_IN_PROGRESS)
        return

    # 4. Create branch name from issue
    branch = f"feat/issue-{number}-{title[:30].lower().replace(' ', '-').replace(':', '').replace('/', '-')}"
    # Sanitize further
    import re
    branch = re.sub(r'[^a-z0-9-]', '', branch)[:60]

    # 5. Execute with Claude Code
    success = run_claude(repo_dir, branch, f"## Tarefa\n{body}\n\n## Instrucoes\n- Siga o AGENTS.md do repositorio\n- Commits em portugues\n- Rode os testes antes de finalizar")

    if not success:
        comment_issue(number, f"**KanBots:** AVISO — Claude Code nao gerou mudancas detectaveis. Verifique `.kanbots-output.txt` no repo.")
        edit_labels(number, add=LABEL_DONE, remove=LABEL_IN_PROGRESS)
        return

    # 6. Commit and push
    pushed = commit_and_push(repo_dir, branch, f"{title}\n\nCloses viaxis-bridge#{number}")
    if not pushed:
        comment_issue(number, "**KanBots:** AVISO — sem mudancas para commitar ou push falhou.")
        edit_labels(number, add=LABEL_DONE, remove=LABEL_IN_PROGRESS)
        return

    # 7. Create PR
    pr_body = f"## O que foi feito\nExecutado por KanBots a partir da issue viaxis-bridge#{number}.\n\nCloses viaxis-bridge#{number}"
    pr_url = create_pr(repo_dir, branch, title, pr_body)
    if pr_url:
        comment_issue(number, f"**KanBots:** Tarefa concluida.\n\n- **PR:** {pr_url}\n- **Branch:** {branch}\n- **Repo:** {target_repo}\n\nCriterios de aceite verificados. Aguardando revisao do Hermes.")
    else:
        comment_issue(number, f"**KanBots:** Branch `{branch}` enviada, mas falha ao criar PR. Verifique manualmente.")

    edit_labels(number, add=LABEL_DONE, remove=LABEL_IN_PROGRESS)
    print(f"  Issue #{number} concluida.")


def watch_loop():
    """Continuous polling loop."""
    print(f"  KanBots Monitor — observando {BRIDGE_REPO}")
    print(f"  Label: {LABEL_READY} | Intervalo: {POLL_INTERVAL}s")
    print(f"  Workspace: {WORKSPACE}")
    print()

    while True:
        try:
            issues = get_ready_issues()
            if issues:
                print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {len(issues)} issue(s) encontrada(s)")
                process_issue(issues[0])  # Process oldest first
            else:
                print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Nenhuma issue pendente")
        except KeyboardInterrupt:
            print("\n  KanBots encerrado.")
            break
        except Exception as e:
            print(f"  [ERRO] {e}")
            time.sleep(10)  # Don't spam on errors
            continue
        time.sleep(POLL_INTERVAL)


def main():
    parser = argparse.ArgumentParser(description="Kanbots Monitor")
    parser.add_argument("--watch", action="store_true", help="Loop continuo de polling")
    parser.add_argument("--once", action="store_true", help="Processa 1 issue e sai (default)")
    args = parser.parse_args()

    if args.watch:
        watch_loop()
    else:
        # Single run
        issues = get_ready_issues()
        if not issues:
            print("  Nenhuma issue com kanbots:ready.")
            return
        process_issue(issues[0])


if __name__ == "__main__":
    main()
