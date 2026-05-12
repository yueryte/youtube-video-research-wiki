#!/usr/bin/env python3
"""
SOP Router - reads sop.yaml, detects changed files, calls the right Hermes webhook.
"""
import os
import sys
import json
import subprocess
import yaml  # pip install pyyaml


def decode_git_path(path: str) -> str:
    """Decode git's quoted paths (used for non-ASCII filenames like Chinese)."""
    path = path.strip()
    if path.startswith('"') and path.endswith('"'):
        # Git encodes non-ASCII as octal escapes inside double quotes
        path = path[1:-1]
        # Decode octal escape sequences: \346\234\254 → bytes → utf-8
        import re
        def replace_octal(m):
            return bytes([int(m.group(0)[1:], 8)]).decode('latin-1')
        path = re.sub(r'\\[0-7]{3}', replace_octal, path)
    return path


def _run_git_diff(diff_filter: str, before_sha: str, after_sha: str) -> list[str]:
    git_env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1'}
    cmd_args = ["-c", "core.quotepath=false", "diff", "--name-only", f"--diff-filter={diff_filter}"]
    try:
        result = subprocess.run(
            ["git"] + cmd_args + [f"{before_sha}..{after_sha}"],
            capture_output=True, text=True, check=True, env=git_env
        )
        return [decode_git_path(f) for f in result.stdout.strip().split("\n") if f.strip()]
    except subprocess.CalledProcessError:
        result = subprocess.run(
            ["git"] + cmd_args + ["HEAD~1..HEAD"],
            capture_output=True, text=True, check=True, env=git_env
        )
        return [decode_git_path(f) for f in result.stdout.strip().split("\n") if f.strip()]


def get_changed_files(before_sha: str, after_sha: str) -> list[str]:
    """Get ADDED/MODIFIED files only (not deleted) between two commits."""
    return _run_git_diff("AM", before_sha, after_sha)


def get_added_files(before_sha: str, after_sha: str) -> list[str]:
    """Get only ADDED files (not modified/deleted) between two commits."""
    return _run_git_diff("A", before_sha, after_sha)


def matches_pattern(filepath: str, pattern: str) -> bool:
    """Simple glob-style pattern matching for file paths."""
    import fnmatch
    return fnmatch.fnmatch(filepath, pattern)


def matches_any(files: list[str], pattern: str) -> bool:
    """Check if any file matches the pattern."""
    return any(matches_pattern(f, pattern) for f in files)


def call_webhook(route: str, payload: dict, base_url: str, secret: str) -> bool:
    """POST payload to Hermes webhook using curl (bypasses Cloudflare bot checks)."""
    url = f"{base_url}/{route}"
    payload_str = json.dumps(payload)
    run_id = payload.get("run_id", "unknown")

    result = subprocess.run([
        "curl", "-sS",
        "-X", "POST", url,
        "-H", "Content-Type: application/json",
        "-H", f"X-Gitlab-Token: {secret}",
        "-H", f"X-Request-ID: {run_id}",
        "-H", "User-Agent: curl/7.81.0",
        "-d", payload_str,
        "-w", "\nHTTP_STATUS:%{http_code}",
        "--max-time", "30",
    ], capture_output=True, text=True)

    output = result.stdout
    print(f"[sop_router] POST {url}\n{output[-300:]}")

    if result.returncode != 0:
        print(f"[sop_router] curl error: {result.stderr}")
        return False

    # Extract HTTP status from output
    for line in output.split("\n"):
        if line.startswith("HTTP_STATUS:"):
            code = int(line.split(":")[1])
            return 200 <= code < 300

    return False


def main():
    # Read environment
    # 支持多个 webhook 地址（逗号分隔），所有地址都会收到通知
    base_urls = [u.strip() for u in os.environ.get(
        "HERMES_WEBHOOK_BASE", "https://hermes-webhooks-212.vyibc.com/webhooks"
    ).split(",") if u.strip()]
    secret = os.environ.get("HERMES_SOP_SECRET", "")
    before_sha = os.environ.get("BEFORE_SHA", "")
    after_sha = os.environ.get("AFTER_SHA", "HEAD")
    run_id = os.environ.get("RUN_ID", "unknown")
    repo = os.environ.get("REPO", "")

    # Load sop.yaml
    with open("sop.yaml", "r") as f:
        sop = yaml.safe_load(f)

    # Get changed files (AM = Added+Modified for analysis; A-only for youtube-links to prevent re-trigger)
    changed = get_changed_files(before_sha, after_sha)
    added_only = get_added_files(before_sha, after_sha)
    print(f"[sop_router] Changed files (AM): {changed}")
    print(f"[sop_router] Added-only files (A): {added_only}")

    if not changed:
        print("[sop_router] No changed files, stopping.")
        return

    # Check terminal paths - if only terminal paths changed, stop
    terminal_paths = sop.get("terminal_paths", [])
    non_terminal = [
        f for f in changed
        if not any(matches_pattern(f, p) for p in terminal_paths)
    ]

    if not non_terminal:
        print(f"[sop_router] Only terminal paths changed ({terminal_paths}), stopping pipeline.")
        return

    # Find matching stage
    # youtube-links: only trigger on truly Added files (prevent re-trigger when file is modified)
    pipeline = sop.get("pipeline", [])
    matched_stage = None

    for stage in pipeline:
        trigger = stage.get("trigger", "")
        if "youtube-links" in trigger:
            if matches_any(added_only, trigger):
                matched_stage = stage
                break
        else:
            if matches_any(changed, trigger):
                matched_stage = stage
                break

    if not matched_stage:
        print(f"[sop_router] No stage matched for changed files: {changed}")
        return

    stage_name = matched_stage["stage"]

    # Guard: Stage D (tg-notify) should only trigger when Stage C just completed.
    # Check that the triggering commit came from Stage C (message contains "wiki graph").
    # This prevents reset commits or manual index.md edits from spuriously triggering Stage D.
    if stage_name == "tg-notify":
        import subprocess as _sp
        commit_msg = _sp.run(
            ["git", "log", "-1", "--format=%s", after_sha],
            capture_output=True, text=True
        ).stdout.strip()
        if "wiki graph" not in commit_msg.lower():
            print(f"[sop_router] Stage D guard: commit '{commit_msg}' is not from Stage C, skipping.")
            return
        print(f"[sop_router] Stage D guard: confirmed Stage C commit, proceeding.")
    webhook_route = matched_stage["webhook_route"]
    stage_params = matched_stage.get("params", {})

    print(f"[sop_router] Matched stage: {stage_name} → route: {webhook_route}")

    # Build payload
    notify = sop.get("notify", {}).get("telegram", {})
    notebooklm_params = stage_params.get("notebooklm", {})

    effective_repo = repo or sop.get("repo", "")
    payload = {
        "stage": stage_name,
        "wiki_local_path": sop.get("wiki_local_path", ""),
        "repo": effective_repo,
        "repo_url": f"https://github.com/{effective_repo}",
        "sha": after_sha,
        "before": before_sha,
        "run_id": run_id,
        "tg_token_env": notify.get("token_env", "TELEGRAM_BOT_TOKEN"),
        "tg_chat_id": notify.get("chat_id", ""),
        # NotebookLM params
        "notebooklm_outputs": notebooklm_params.get("outputs", ["report", "mindmap"]),
        "notebooklm_language": notebooklm_params.get("language", "zh_Hans"),
        "notebooklm_notebook_title": notebooklm_params.get("notebook_title", ""),
        "notebooklm_report_prompt": notebooklm_params.get("report_prompt", ""),
        "notebooklm_mindmap_prompt": notebooklm_params.get("mindmap_prompt", ""),
        # Wiki build params
        "build_mode": stage_params.get("build_mode", "incremental"),
    }

    # 广播到所有配置的 webhook 地址
    results = []
    for base_url in base_urls:
        ok = call_webhook(webhook_route, payload, base_url, secret)
        results.append((base_url, ok))

    succeeded = [u for u, ok in results if ok]
    failed = [u for u, ok in results if not ok]

    if failed:
        print(f"[sop_router] Failed endpoints ({len(failed)}): {failed}")
    if not succeeded:
        print(f"[sop_router] All webhook calls failed for stage {stage_name}")
        sys.exit(1)

    print(f"[sop_router] Successfully triggered stage: {stage_name} → {len(succeeded)}/{len(results)} endpoints")


if __name__ == "__main__":
    main()
