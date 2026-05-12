#!/usr/bin/env python3
"""
SOP Stage C: Wiki Builder v3
- 2步并行生成（Step1提取大纲，Step2并行生成各页面）
- git diff 只处理本次新增文件
- 增量更新 index.md（不覆写）
- 脑图节点注入 prompt
- 调用 verify-quality.sh 质检
- 写 pipeline-context.json
"""
import argparse, json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


# ── LLM ────────────────────────────────────────────────────────
def call_llm(prompt: str, max_tokens: int = 3000, timeout: int = 180) -> tuple:
    """返回 (text, prompt_tokens, completion_tokens, elapsed_s)"""
    import time, urllib.request

    # 优先 DeepSeek（质量更高），unset DASHSCOPE 防止被 qwen 覆盖
    if os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("FORCE_QWEN"):
        api_key = os.environ["DEEPSEEK_API_KEY"]
        base_url = "https://api.deepseek.com/v1"
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    elif os.environ.get("DASHSCOPE_API_KEY"):
        api_key = os.environ["DASHSCOPE_API_KEY"]
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        model = "qwen-turbo"
    else:
        raise RuntimeError("No API key: set DEEPSEEK_API_KEY or DASHSCOPE_API_KEY")

    t0 = time.time()
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.3
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/chat/completions", data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    elapsed = time.time() - t0
    text = data["choices"][0]["message"].get("content") or ""
    usage = data.get("usage", {})
    return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), elapsed


def parse_json_safe(text: str) -> dict:
    text = re.sub(r'^```json\s*', '', text.strip(), flags=re.MULTILINE)
    text = re.sub(r'^```\s*$', '', text.strip(), flags=re.MULTILINE)
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        s, e = text.find('{'), text.rfind('}')
        if s >= 0 and e > s:
            return json.loads(text[s:e+1])
        raise


def strip_code_fence(text: str) -> str:
    """剥掉 LLM 可能输出的 ```markdown / ```yaml 包裹，返回纯 markdown"""
    text = text.strip()
    # 去掉开头的 ```markdown / ```yaml / ``` 等
    text = re.sub(r'^```[a-zA-Z]*\n', '', text)
    # 去掉结尾的 ```
    text = re.sub(r'\n```\s*$', '', text)
    return text.strip()


# ── 文件工具 ─────────────────────────────────────────────────────
def git_run(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def get_new_report_files(wiki_path: Path, before_sha: str, sha: str) -> list[Path]:
    """用 git diff 找本次新增的分析文件，fallback 到最近修改的文件"""
    if before_sha and before_sha != sha:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "diff", "--name-only",
             "--diff-filter=AM", before_sha, sha, "--",
             "raw/notebooklm-analysis/"],
            cwd=wiki_path, capture_output=True, text=True
        )
        files = [wiki_path / l.strip() for l in result.stdout.splitlines()
                 if l.strip().endswith(".md")]
        files = [f for f in files if f.exists()]
        if files:
            return sorted(files)

    # fallback: 取最近1个
    all_files = sorted(
        (wiki_path / "raw/notebooklm-analysis").glob("*.md"),
        key=lambda f: f.stat().st_mtime, reverse=True
    )
    return all_files[:1]


def load_mindmap_nodes(wiki_path: Path, report_stem: str) -> str:
    """读脑图 JSON，提取前25个节点文本"""
    mindmap_file = wiki_path / "raw/notebooklm-mindmaps" / (report_stem + ".json")
    if not mindmap_file.exists():
        return ""
    try:
        mm = json.loads(mindmap_file.read_text(encoding="utf-8"))
        nodes = mm.get("nodes", [])[:25]
        return "\n".join(f"- {n.get('text', '')}" for n in nodes if n.get("text"))
    except Exception:
        return ""


def update_index_incremental(wiki_path: Path, new_entries: dict):
    """增量更新 index.md，只追加新条目，不覆写已有内容"""
    idx_file = wiki_path / "index.md"
    existing = idx_file.read_text(encoding="utf-8") if idx_file.exists() else ""

    def _section_entries(text: str, section: str) -> set:
        lines = text.split("\n")
        in_sec = False
        entries = set()
        for line in lines:
            if line.startswith(f"## {section}"):
                in_sec = True
                continue
            if in_sec and line.startswith("## "):
                break
            if in_sec and line.startswith("- [["):
                m = re.search(r'\[\[([^\]|]+)', line)
                if m:
                    entries.add(m.group(1).lower())
        return entries

    for section, items in new_entries.items():
        existing_slugs = _section_entries(existing, section)
        to_add = []
        for item in items:
            m = re.search(r'\[\[([^\]|]+)', item)
            slug = m.group(1).lower() if m else ""
            if slug and slug not in existing_slugs:
                to_add.append(item)
        if to_add:
            sec_header = f"## {section}"
            if sec_header in existing:
                existing = existing.replace(
                    sec_header,
                    sec_header + "\n" + "\n".join(to_add),
                    1
                )
            else:
                existing += f"\n{sec_header}\n" + "\n".join(to_add) + "\n"

    # 更新 Last updated 和 Total pages
    total = len(list((wiki_path / "wiki").rglob("*.md")))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing = re.sub(r'Last updated:.*', f'Last updated: {today}', existing)
    existing = re.sub(r'Total pages: \d+', f'Total pages: {total}', existing)
    idx_file.write_text(existing, encoding="utf-8")


# ── 核心生成逻辑 ─────────────────────────────────────────────────
def step1_outline(schema: str, report_content: str, report_name: str,
                  mindmap: str, index_content: str) -> dict:
    """Step 1：快速提取大纲（页面列表）"""
    prompt = f"""分析视频研究报告，按知识图谱规范列出需要创建的页面。

## TheSchema 规范摘要
{schema[:2000]}

## 现有索引（避免重复）
{index_content[:400]}

## 报告：{report_name}
{report_content[:3500]}

## 脑图节点
{mindmap[:800]}

返回纯 JSON（不要代码块标记）：
{{"source":{{"title":"页面标题","summary":"一句话摘要(≤25字)"}},"entities":[{{"name":"实体名","desc":"≤15字"}}],"concepts":[{{"name":"概念名","desc":"≤15字"}}],"comparisons":[{{"name":"比较名","desc":"≤15字"}}]}}

规则：实体≤4个，概念≤5个，比较≤2个。只列本报告独有内容，不列已在现有索引的内容。"""

    text, pt, ct, el = call_llm(prompt, max_tokens=1500)
    outline = parse_json_safe(text)
    print(f"[wiki-builder] Step1 done {el:.0f}s | {pt+ct}tok | "
          f"entity×{len(outline.get('entities',[]))} "
          f"concept×{len(outline.get('concepts',[]))} "
          f"comparison×{len(outline.get('comparisons',[]))}")
    return outline, pt, ct


def step2_gen_page(page_type: str, name: str, desc: str,
                   report_content: str, schema: str) -> tuple:
    """Step 2：生成单个页面（并行调用）"""
    type_map = {
        "source":     ("wiki/sources",     "视频来源"),
        "entity":     ("wiki/entities",    "实体"),
        "concept":    ("wiki/concepts",    "概念"),
        "comparison": ("wiki/comparisons", "对比"),
    }
    dir_path, type_label = type_map[page_type]

    prompt = f"""为知识图谱生成一个{type_label}页面。

页面名称：{name}
说明：{desc}

参考报告：
{report_content[:3000]}

TheSchema 核心规范：
{schema[:800]}

输出要求：
- YAML frontmatter 必须包含全部字段（缺一不可）：
  title, type, tags, summary（一句话摘要≤25字）, sources, created, updated, layer
- 正文 ≥400字，包含报告中的具体技术细节和例子
- 包含 ≥3 个 [[wikilink]] 指向相关页面
- 全中文（英文专有名词保留）
- 直接输出纯 markdown，不要用代码块包裹

直接输出完整 markdown，不要额外说明。"""

    text, pt, ct, el = call_llm(prompt, max_tokens=3000)
    text = strip_code_fence(text)
    slug = re.sub(r'[^\w一-鿿-]', '-', name).strip('-')
    path = f"{dir_path}/{slug}.md"
    return path, text, pt, ct, el


# ── 主流程 ───────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wiki-path", required=True)
    parser.add_argument("--run-id",    required=True)
    parser.add_argument("--before-sha", default="")
    parser.add_argument("--sha",       default="HEAD")
    parser.add_argument("--no-commit", action="store_true")
    args = parser.parse_args()

    wiki_path = Path(args.wiki_path).expanduser()
    run_id    = args.run_id
    t_start   = datetime.now(timezone.utc)

    print(f"[wiki-builder] Starting run {run_id}")

    # Phase 1: 防冲突同步 git pull --ff-only
    stash = git_run(["stash"], wiki_path)
    git_run(["fetch", "origin"], wiki_path)
    pull = git_run(["pull", "--ff-only", "origin", "main"], wiki_path)
    if pull.returncode != 0:
        print(f"[wiki-builder] Warning: pull --ff-only failed: {pull.stderr.strip()}")
    if stash.stdout.strip() != "No local changes to save":
        git_run(["stash", "pop"], wiki_path)

    # 读取 schema 和 index
    schema = (wiki_path / "TheSchema.md").read_text(encoding="utf-8")
    index_content = (wiki_path / "index.md").read_text(encoding="utf-8") \
        if (wiki_path / "index.md").exists() else ""

    # 找本次新增的报告
    report_files = get_new_report_files(wiki_path, args.before_sha, args.sha)
    if not report_files:
        print("[wiki-builder] No new analysis reports, skipping.")
        # 还是要写 skip log
        _write_skip_log(wiki_path, run_id)
        if not args.no_commit:
            git_run(["add", f"logs/webhook-runs/{run_id}.md"], wiki_path)
            git_run(["commit", "-m", f"chore: skip wiki build (no new analysis files) [run:{run_id}]"], wiki_path)
            git_run(["push", "origin", "main"], wiki_path)
        sys.exit(0)

    print(f"[wiki-builder] {len(report_files)} new report(s): {[f.name for f in report_files]}")

    all_written = []
    all_index_entries = {"Sources": [], "Entities": [], "Concepts": [], "Comparisons": [], "Overview": []}
    total_stats = {"sources": 0, "entities": 0, "concepts": 0, "comparisons": 0, "overviews": 0}
    total_pt = total_ct = total_calls = 0

    for report_file in report_files:
        report_content = report_file.read_text(encoding="utf-8")
        mindmap_nodes  = load_mindmap_nodes(wiki_path, report_file.stem)

        print(f"[wiki-builder] [{report_files.index(report_file)+1}/{len(report_files)}] "
              f"Step1 outline → {report_file.name}")

        # Step 1: 提取大纲
        outline, pt1, ct1 = step1_outline(
            schema, report_content, report_file.name,
            mindmap_nodes, index_content
        )
        total_pt += pt1; total_ct += ct1; total_calls += 1

        # 构建任务列表
        tasks = []
        src = outline.get("source", {})
        if src:
            tasks.append(("source", src.get("title", report_file.stem), src.get("summary", "")))
        for e in outline.get("entities", []):
            tasks.append(("entity",     e["name"], e.get("desc", "")))
        for c in outline.get("concepts", []):
            tasks.append(("concept",    c["name"], c.get("desc", "")))
        for x in outline.get("comparisons", []):
            tasks.append(("comparison", x["name"], x.get("desc", "")))

        print(f"[wiki-builder] [{report_files.index(report_file)+1}/{len(report_files)}] "
              f"Step2 parallel generate {len(tasks)} pages...")

        # Step 2: 并行生成
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(step2_gen_page, ptype, name, desc, report_content, schema): (ptype, name)
                for ptype, name, desc in tasks
            }
            for future in as_completed(futures):
                ptype, name = futures[future]
                try:
                    path, content, pt, ct, el = future.result()
                    total_pt += pt; total_ct += ct; total_calls += 1

                    # 写文件
                    out_file = wiki_path / path
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    out_file.write_text(content, encoding="utf-8")
                    all_written.append(path)

                    wl = len(re.findall(r'\[\[', content))
                    print(f"[wiki-builder]   -> {path} ({len(content)}字 {wl}links {el:.0f}s)")

                    # 统计
                    type_key = ptype + "s"
                    if type_key in total_stats:
                        total_stats[type_key] += 1

                    # 收集 index 条目
                    sec_map = {"source":"Sources","entity":"Entities",
                               "concept":"Concepts","comparison":"Comparisons"}
                    section = sec_map.get(ptype, "Sources")
                    m = re.search(r'^title:\s*(.+)$', content, re.MULTILINE)
                    title = m.group(1).strip() if m else name
                    slug = re.sub(r'[^\w一-鿿-]', '-', name).strip('-')
                    all_index_entries[section].append(f"- [[{slug}|{title}]]")

                except Exception as e:
                    print(f"[wiki-builder]   ✗ {ptype}/{name}: {e}")

    t_end = datetime.now(timezone.utc)
    duration = int((t_end - t_start).total_seconds())

    print(f"[wiki-builder] {len(all_written)} pages | {total_calls} API calls | "
          f"{total_pt+total_ct}tok | {duration}s")

    if not all_written:
        print("[wiki-builder] No pages written, skipping commit.")
        sys.exit(0)

    # 增量更新 index.md
    update_index_incremental(wiki_path, all_index_entries)

    # 写 pipeline-context.json
    ctx_file = wiki_path / "raw/pipeline-context.json"
    ctx = {}
    try:
        ctx = json.loads(ctx_file.read_text())
    except Exception:
        pass
    ctx["stage_c"] = {
        "run_id": run_id,
        "task_id": f"T-{run_id.split('-')[1] if '-' in run_id else run_id}",
        "start_time": t_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time":   t_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_s": duration,
        "api_calls":  total_calls,
        "pages_created": len(all_written),
        **{k: total_stats[k] for k in total_stats}
    }
    ctx_file.write_text(json.dumps(ctx, ensure_ascii=False, indent=2))

    # 写 run log
    log_dir = wiki_path / "logs/webhook-runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_content = f"""---
run_id: {run_id}
stage: stage_c
duration_s: {duration}
api_calls: {total_calls}
pages_created: {len(all_written)}
---\n\n""" + "\n".join(f"- {p}" for p in all_written)
    (log_dir / f"{run_id}.md").write_text(log_content)

    # 质检
    verify_script = Path(__file__).parent / "verify-quality.sh"
    if verify_script.exists():
        print("[wiki-builder] Running quality check...")
        r = subprocess.run(["bash", str(verify_script), str(wiki_path)],
                           capture_output=True, text=True)
        print(r.stdout[-500:] if r.stdout else "")
        if r.returncode != 0:
            print(f"[wiki-builder] Quality check warnings:\n{r.stderr[-300:]}")

    if args.no_commit:
        print(f"[wiki-builder] --no-commit: skipping git commit (task_id={ctx['stage_c']['task_id']})")
        print(f"TASK_ID={ctx['stage_c']['task_id']}")
    else:
        # git commit + push
        git_run(["add", "wiki/", "index.md", "log.md", "logs/",
                 "raw/pipeline-context.json"], wiki_path)
        diff = git_run(["diff", "--cached", "--quiet"], wiki_path)
        if diff.returncode != 0:
            git_run(["-c", "user.email=hermes@vyibc.com", "-c", "user.name=Hermes",
                     "commit", "-m",
                     f"chore: update wiki graph [run:{run_id}]"], wiki_path)
            for _ in range(3):
                r = git_run(["push", "origin", "main"], wiki_path)
                if r.returncode == 0:
                    print("[wiki-builder] Push successful")
                    break
                git_run(["pull", "--ff-only", "origin", "main"], wiki_path)

    print(f"\n[wiki-builder] Done: {len(all_written)} pages | {total_calls} API calls | {duration}s")
    print(json.dumps({"status": "success", "pages": len(all_written),
                      "api_calls": total_calls, "duration_s": duration}))


def _write_skip_log(wiki_path: Path, run_id: str):
    log_dir = wiki_path / "logs/webhook-runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{run_id}.md").write_text(
        f"---\nrun_id: {run_id}\nstage: stage_c\nstatus: skipped\n---\nskipped:no_new_analysis_files\n"
    )


if __name__ == "__main__":
    main()
