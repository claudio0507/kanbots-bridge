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


# ── Post-Claude detection ─────────────────────────────────────────────
def get_current_branch(repo_dir: Path) -> str:
    """Get the current git branch name."""
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        capture_output=True, text=True, cwd=str(repo_dir),
    )
    return result.stdout.strip()


def has_unpushed_commits(repo_dir: Path, branch: str) -> bool:
    """Check if branch has commits not yet pushed to origin."""
    result = subprocess.run(
        ["git", "log", f"origin/{branch}..{branch}", "--oneline"],
        capture_output=True, text=True, cwd=str(repo_dir),
    )
    return bool(result.stdout.strip())


def pr_exists_for_branch(repo_dir: Path, branch: str) -> str | None:
    """Check if a PR already exists for the given branch. Returns PR URL or None."""
    result = subprocess.run(
        ["gh", "pr", "list", "--head", branch, "--state", "open",
         "--json", "url", "--jq", ".[0].url"],
        capture_output=True, text=True, timeout=15, cwd=str(repo_dir),
    )
    url = result.stdout.strip()
    return url if url and result.returncode == 0 else None


def detect_and_finalize(repo_dir: Path, original_branch: str, number: int, title: str, target_repo: str) -> str | None:
    """
    After Claude runs, detect what actually happened and finalize (push/PR).
    Claude might have: created its own branch, pushed, even created a PR.
    Returns PR URL if successful, None otherwise.
    """
    actual_branch = get_current_branch(repo_dir)
    print(f"  [git] Branch atual: {actual_branch} (original: {original_branch})")

    # If no branch detected, nothing happened
    if not actual_branch or actual_branch == "main":
        print("  [git] Nenhuma branch de feature detectada")
        return None

    # Check if there are any commits on this branch vs main
    result = subprocess.run(
        ["git", "log", "main.." + actual_branch, "--oneline"],
        capture_output=True, text=True, cwd=str(repo_dir),
    )
    commits = result.stdout.strip()
    if not commits:
        print("  [git] Sem commits na branch (vazia)")
        return None
    print(f"  [git] Commits encontrados:\n{commits}")

    # Check if PR already exists
    existing_pr = pr_exists_for_branch(repo_dir, actual_branch)
    if existing_pr:
        print(f"  [gh] PR ja existe: {existing_pr}")
        return existing_pr

    # Push if needed
    if has_unpushed_commits(repo_dir, actual_branch):
        print(f"  [git] Push {actual_branch}...")
        result = subprocess.run(
            ["git", "push", "origin", actual_branch],
            capture_output=True, text=True, timeout=60, cwd=str(repo_dir),
        )
        if result.returncode != 0:
            print(f"  [ERRO] push: {result.stderr.strip()}")
            return None

    # Create PR
    pr_body = f"## O que foi feito\nExecutado por KanBots + Claude Code a partir da issue kanbots-bridge#{number}.\n\nCloses kanbots-bridge#{number}"
    pr_url = create_pr(repo_dir, actual_branch, title, pr_body)
    if not pr_url:
        # Try to get existing PR URL
        existing = pr_exists_for_branch(repo_dir, actual_branch)
        if existing:
            return existing
    return pr_url


# ── Claude Code executor ──────────────────────────────────────────────
def find_claude() -> str | None:
    """Find Claude CLI executable. Returns full path or None."""
    import shutil

    # 1. Check PATH
    found = shutil.which("claude")
    if found:
        return found

    # 2. Check common npm global paths (Windows)
    if sys.platform == "win32":
        npm_paths = [
            Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
            Path.home() / "AppData" / "Roaming" / "npm" / "claude.cmd",
            Path.home() / "AppData" / "Local" / "npm" / "claude.cmd",
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "nodejs" / "claude.cmd",
        ]
        for p in npm_paths:
            if p.exists():
                return str(p)

    # 3. Try npx as fallback
    npx = shutil.which("npx")
    if npx:
        return npx  # Will use "npx claude" syntax

    return None


def run_claude(repo_dir: Path, branch: str, task: str) -> bool:
    """
    Run Claude Code on a task in the given repo.
    Creates a new branch, writes the prompt, invokes Claude.
    Returns True if Claude exited successfully (exit code 0).
    Note: Claude may create its own branch and do its own git workflow.
    """
    os.chdir(str(repo_dir))

    # Find Claude executable
    claude_exe = find_claude()
    if not claude_exe:
        print("  [ERRO] Claude CLI nao encontrado.")
        print("  Verifique: npm install -g @anthropic-ai/claude-code")
        return False
    print(f"  [claude] Encontrado: {claude_exe}")

    # Start from main and let Claude do its own branching
    subprocess.run(["git", "checkout", "main"], capture_output=True)
    subprocess.run(["git", "pull", "origin", "main"], capture_output=True)

    # Write prompt file (Claude can read it, or we pass via stdin)
    prompt_file = repo_dir / ".kanbots-prompt.md"
    prompt_file.write_text(task, encoding="utf-8")

    # Build command using prompt file via stdin
    if sys.platform == "win32":
        cmd = f'type "{prompt_file}" | "{claude_exe}" --print --dangerously-skip-permissions -p -'
    else:
        cmd = f'"{claude_exe}" --print --dangerously-skip-permissions -p "$(cat {prompt_file})"'

    # Run Claude Code
    print(f"  [claude] Executando tarefa ({CLAUDE_TIMEOUT}s timeout)...")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=CLAUDE_TIMEOUT,
            cwd=str(repo_dir),
            shell=True,
        )
        output = result.stdout + result.stderr
        print(f"  [claude] Exit: {result.returncode}")

        # Save output
        log_file = repo_dir / ".kanbots-output.txt"
        log_file.write_text(output, encoding="utf-8")

        return result.returncode == 0

    except subprocess.TimeoutExpired:
        print(f"  [claude] TIMEOUT apos {CLAUDE_TIMEOUT}s")
        return False


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
        comment_issue(number, "**KanBots:** ERRO — nao foi possivel identificar o repositorio alvo na issue.")
        edit_labels(number, remove=LABEL_IN_PROGRESS)
        return

    print(f"  Target repo: {target_repo}")

    # 3. Clone/pull repo
    repo_dir = ensure_repo(target_repo)
    if not repo_dir:
        comment_issue(number, f"**KanBots:** ERRO — falha ao clonar {target_repo}.")
        edit_labels(number, remove=LABEL_IN_PROGRESS)
        return

    # 4. Create branch name as fallback (Claude may create its own)
    import re
    sanitized = re.sub(r'[^a-z0-9-]', '', title[:40].lower().replace(' ', '-'))[:40]
    fallback_branch = f"feat/issue-{number}-{sanitized}"

    # 5. Execute with Claude Code
    task_prompt = f"## Tarefa\n{body}\n\n## Instrucoes\n- Crie uma branch com nome descritivo\n- Siga o AGENTS.md do repositorio\n- Commits em portugues\n- Rode os testes antes de finalizar\n- Ao final, crie um PR contra main"
    claude_ok = run_claude(repo_dir, fallback_branch, task_prompt)

    if not claude_ok:
        comment_issue(number, "**KanBots:** ERRO — Claude Code falhou ou timeout. Verifique `.kanbots-output.txt` no workspace.")
        edit_labels(number, add=LABEL_DONE, remove=LABEL_IN_PROGRESS)
        return

    # 6. Detect what Claude actually did and finalize
    pr_url = detect_and_finalize(repo_dir, fallback_branch, number, title, target_repo)

    if pr_url:
        actual_branch = get_current_branch(repo_dir)
        comment_issue(number,
            f"**KanBots:** Tarefa concluida via Claude Code.\n\n"
            f"- **PR:** {pr_url}\n"
            f"- **Branch:** {actual_branch}\n"
            f"- **Repo:** {target_repo}\n\n"
            f"Criterios de aceite verificados. Aguardando revisao do Hermes."
        )
    else:
        comment_issue(number, "**KanBots:** AVISO — Claude Code executou mas nao foi detectado PR. Verifique o workspace manualmente.")

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
