#!/usr/bin/env python3
"""
SOP Stage D: TG Notify - 通用版
优先读 pipeline-context.json 里的 tg_summary（各 wiki 自定义内容）
无 tg_summary 时 fallback 到 youtube-wiki 的旧逻辑（向后兼容）
"""
import json, os, sys, subprocess, shutil
from pathlib import Path


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    result = subprocess.run([
        "curl", "-s", "-X", "POST",
        f"https://api.telegram.org/bot{token}/sendMessage",
        "-d", f"chat_id={chat_id}",
        "-d", "disable_web_page_preview=true",
        "--data-urlencode", f"text={text}",
        "-w", "\nHTTP_STATUS:%{http_code}"
    ], capture_output=True, text=True)
    return "HTTP_STATUS:200" in result.stdout


def git_run(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def load_output_check(wiki_path: Path) -> dict:
    """读取 output-check 结果"""
    f = wiki_path / "raw" / "output-check-result.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def format_output_check(qc: dict) -> str:
    """格式化 output check 结果为 TG 可读文本"""
    if not qc:
        return ""

    verdict = qc.get("verdict", "unknown")
    icons = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
    icon = icons.get(verdict, "❓")

    must = qc.get("must_pass", {})
    warns = qc.get("warnings", {})
    details = qc.get("details", [])

    lines = [f"\n{icon} Output Check: {verdict.upper()}"]

    check_map = {
        "has_analysis":      "分析文件",
        "report_length_ok":  "报告字数",
        "title_match":       "内容相符",
        "has_wiki_source":   "wiki 页面",
        "concept_depth":     "概念深度",
        "wikilink_density":  "连通性",
        "has_datasource":    "datasource",
    }

    for k, label in check_map.items():
        if k in must:
            lines.append(f"  {'✅' if must[k] else '❌'} {label}")
        elif k in warns:
            lines.append(f"  {'✅' if warns[k] else '⚠️'} {label}")

    return "\n".join(lines)


def format_commands(qc: dict, video_id: str) -> str:
    """根据质量结果生成可用指令"""
    if not qc:
        return ""

    verdict = qc.get("verdict", "pass")
    vid = video_id or "VIDEO_ID"

    if verdict == "pass":
        return f"\n[可选操作]\n  /retry-C {vid}  重建 wiki（当前质量已通过）"
    elif verdict == "warn":
        return (f"\n[建议操作]\n"
                f"  /retry-C {vid}  重建 wiki（改善概念深度/连通性）\n"
                f"  /ok {vid}       确认接受当前质量")
    else:  # fail
        return (f"\n[需要操作]\n"
                f"  /retry-B {vid}  重新研究（推荐）\n"
                f"  /retry-C {vid}  仅重建 wiki\n"
                f"  /skip {vid}     跳过，保留低质量内容")


def build_youtube_wiki_msg(ctx, wiki_path, run_id, repo_url):
    """youtube-wiki 专用消息格式"""
    stage_b = ctx.get("stage_b", {})
    stage_c = ctx.get("stage_c", {})

    # 优先从 pipeline-context.json 读本次处理的视频（避免显示全部历史）
    sources = []
    processed_urls = stage_b.get("processed_urls", [])
    sources_dir = wiki_path / "wiki/sources"
    if processed_urls and sources_dir.exists():
        # 只显示本次处理的视频
        for src_file in sorted(sources_dir.glob("*.md")):
            src_content = src_file.read_text(encoding="utf-8")
            title = video_url = ""
            for line in src_content.split("\n"):
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
                if line.startswith("video_url:"):
                    video_url = line.split(":", 1)[1].strip()
            # 只加入本次处理过的 URL
            if title and any(video_url in u or u in video_url for u in processed_urls):
                sources.append({"title": title, "url": video_url, "file": src_file.stem})
    elif sources_dir.exists():
        # fallback: 无 processed_urls 时，只显示最近修改的 1 个 source（不显示全部历史）
        recent = sorted(sources_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        for src_file in recent[:1]:
            src_content = src_file.read_text(encoding="utf-8")
            title = video_url = ""
            for line in src_content.split("\n"):
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
                if line.startswith("video_url:"):
                    video_url = line.split(":", 1)[1].strip()
            if title:
                sources.append({"title": title, "url": video_url, "file": src_file.stem})

    total_dur = stage_b.get("duration_s", 0) + stage_c.get("duration_s", 0)
    video_lines = "\n".join(f"  {i+1}. {s['title']}" for i, s in enumerate(sources))
    nav_lines = ""
    if repo_url:
        for s in sources:
            encoded = s['file'].replace(" ", "%20")
            nav_lines += f"  · {s['title'][:25]}...\n    {repo_url}/blob/main/wiki/sources/{encoded}.md\n"

    task_id = stage_c.get("task_id") or f"T-{run_id}"

    # Output Check 结果
    qc = load_output_check(wiki_path)
    qc_text = format_output_check(qc)

    # 视频 ID（从 sources 或 processed_urls 提取）
    video_id = ""
    if sources:
        import re
        m = re.search(r'[a-zA-Z0-9_-]{6,}', sources[0].get('url', '').split('?')[0].split('/')[-1])
        if m:
            video_id = m.group()
    commands_text = format_commands(qc, video_id)

    # 流程标识
    retry_count = ctx.get("retry_count", 0)
    flow_icon = "✅ 一遍过" if retry_count == 0 else f"♻️ 返工 {retry_count} 次"

    return f"""[YOUTUBE-WIKI] #{task_id}  {flow_icon}

📹 {sources[0]['title'] if sources else '本次处理视频'}
🔗 {sources[0]['url'] if sources else ''}
{qc_text}
⏱️ 耗时：B {stage_b.get('duration_s', '?')}s / C {stage_c.get('duration_s', '?')}s / 合计 {total_dur}s

📊 知识图谱：
  Source {stage_c.get('sources', 0)} / Entity {stage_c.get('entities', 0)} / Concept {stage_c.get('concepts', 0)} / 总 {stage_c.get('pages_created', 0)} 页

🔗 入口：
{nav_lines if nav_lines else '  (未配置仓库 URL)'}
{commands_text}
run_id: {run_id}"""


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--wiki-path", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--tg-token-env", default="YOUTUBE_WIKI_TG_TOKEN")
    parser.add_argument("--tg-chat-id", required=True)
    parser.add_argument("--repo-url", default="")
    args = parser.parse_args()

    wiki_path = Path(args.wiki_path).expanduser()
    token = os.environ.get(args.tg_token_env, "")

    if not token:
        print(f"[sop_tg_notify] No token found in env var {args.tg_token_env}")
        sys.exit(1)

    # 读取 pipeline-context.json
    ctx_file = wiki_path / "raw/pipeline-context.json"
    if not ctx_file.exists():
        logs = sorted((wiki_path / "logs/pipeline-runs").glob("*.json"),
                      key=lambda f: f.stat().st_mtime, reverse=True)
        ctx_file = logs[0] if logs else None

    if not ctx_file or not ctx_file.exists():
        print("[sop_tg_notify] No pipeline-context.json found")
        sys.exit(1)

    ctx = json.loads(ctx_file.read_text())

    # ── 消息构建：优先用 tg_summary，没有则 fallback 到 youtube-wiki 格式 ──
    if ctx.get("tg_summary"):
        msg = ctx["tg_summary"]
        print("[sop_tg_notify] Using custom tg_summary from pipeline-context.json")
    else:
        msg = build_youtube_wiki_msg(ctx, wiki_path, args.run_id, args.repo_url)
        print("[sop_tg_notify] Using youtube-wiki default message format")

    # 发送
    success = send_telegram(token, args.tg_chat_id, msg)
    print(f"[sop_tg_notify] TG sent: {success}")

    # 归档
    archive_dir = wiki_path / "logs/pipeline-runs"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_file = archive_dir / f"pipe-{args.run_id}.json"
    if ctx_file.resolve() != archive_file.resolve():
        shutil.copy(ctx_file, archive_file)
    if ctx_file.name == "pipeline-context.json":
        ctx_file.unlink()

    # 写 run-log
    (wiki_path / f"logs/webhook-runs/{args.run_id}.md").write_text(
        f"---\nrun_id: {args.run_id}\nstage: stage_d\n"
        f"status: {'success' if success else 'tg_failed'}\ntg_sent: {success}\n"
        f"archived: logs/pipeline-runs/pipe-{args.run_id}.json\n---\n",
        encoding="utf-8"
    )

    # git commit + push
    git_run(["add", "-A"], wiki_path)
    if git_run(["status", "--porcelain"], wiki_path).stdout.strip():
        git_run(["commit", "-m", f"chore: tg notify done [run:{args.run_id}]"], wiki_path)
        for _ in range(3):
            if git_run(["push", "origin", "main"], wiki_path).returncode == 0:
                break
            git_run(["pull", "--rebase", "origin", "main"], wiki_path)

    print("[sop_tg_notify] Done")


if __name__ == "__main__":
    main()
