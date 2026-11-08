"""
Multilingual prompt-injection benchmark - experiment runner.

  python main.py                      # run everything (450 model calls), then evaluate + report
  python main.py --limit 3            # quick test: first 3 prompts of each language
  python main.py --models qwen3:8b    # only one model
  python main.py --evaluate-only      # re-score saved answers without calling the models
  python main.py --judge MODEL        # also let a separate local model re-check ambiguous cases
  python main.py --retry-errors       # call the model again for answers that ended in ERROR

The run can be stopped at any time (Ctrl+C). Every finished answer is saved
immediately to results/raw_responses.jsonl, and the next run continues where
it stopped.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime

import requests

import config
from evaluator import evaluate, judge_ambiguous
from report import make_report

RESULT_COLUMNS = [
    "model_name", "prompt_id", "language", "attack_category", "difficulty", "prompt",
    "model_response", "evaluation", "failure_reason",
    # extra columns that help with analysis
    "pair_id", "attack_vector", "attack_goal", "attack_marker", "eval_method",
    "needs_review", "response_seconds", "output_tokens", "truncated",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def out_path(name):
    return os.path.join(config.RESULTS_DIR, name)


def log_error(message):
    line = f"{datetime.now().isoformat(timespec='seconds')}  {message}"
    print("   ERROR:", message)
    with open(out_path(config.ERROR_LOG_FILE), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_datasets(languages, limit=None):
    rows = []
    for language, path in config.DATASET_FILES.items():
        if languages and language not in languages:
            continue
        with open(path, encoding="utf-8") as f:
            data = list(csv.DictReader(f))
        if len(data) != 50:
            print(f"Warning: {path} has {len(data)} prompts (expected 50).")
        rows.extend(data[:limit] if limit else data)
    return rows


def load_jsonl(path):
    records = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    records[(rec["model_name"], rec["prompt_id"])] = rec  # later lines win
    return records


def append_jsonl(path, record):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------
def installed_models():
    r = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=10)
    r.raise_for_status()
    return {m["name"]: m for m in r.json()["models"]}


def ask_model(model_cfg, prompt):
    """Send one prompt (with the trusted system prompt) to a model.
    Returns a dict with the answer or an error message."""
    body = {
        "model": model_cfg["name"],
        "messages": [
            {"role": "system", "content": config.SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": config.GENERATION_OPTIONS,
        "keep_alive": "30m",
    }
    if "think" in model_cfg:
        body["think"] = model_cfg["think"]

    last_error = ""
    for attempt in range(1, config.MAX_RETRIES + 2):
        start = time.time()
        try:
            r = requests.post(f"{config.OLLAMA_URL}/api/chat", json=body,
                              timeout=config.REQUEST_TIMEOUT_SECONDS)
            if r.status_code != 200:
                last_error = f"HTTP {r.status_code}: {r.text[:300]}"
            else:
                data = r.json()
                text = data.get("message", {}).get("content", "")
                # Safety net: drop any reasoning block a model may still print
                text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
                return {
                    "response": text,
                    "error": "" if text else "empty response",
                    "response_seconds": round(time.time() - start, 1),
                    "output_tokens": data.get("eval_count", 0),
                    "truncated": data.get("done_reason") == "length",
                }
        except requests.exceptions.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt <= config.MAX_RETRIES:
            time.sleep(5 * attempt)
    return {"response": "", "error": last_error, "response_seconds": 0,
            "output_tokens": 0, "truncated": False}


def unload_model(name):
    """Free the RAM before the next model is loaded."""
    try:
        requests.post(f"{config.OLLAMA_URL}/api/generate",
                      json={"model": name, "keep_alive": 0}, timeout=60)
    except requests.exceptions.RequestException:
        pass


# ---------------------------------------------------------------------------
# Step 1: collect answers
# ---------------------------------------------------------------------------
def run_models(models, rows, retry_errors):
    raw_path = out_path(config.RAW_RESPONSES_FILE)
    done = load_jsonl(raw_path)

    for model_cfg in models:
        name = model_cfg["name"]
        todo = [r for r in rows
                if (name, r["id"]) not in done
                or (retry_errors and done[(name, r["id"])]["error"])]
        print(f"\n=== {name}: {len(rows) - len(todo)} already done, {len(todo)} to run ===")
        times = []
        for i, row in enumerate(todo, start=1):
            result = ask_model(model_cfg, row["prompt"])
            record = {"model_name": name, "prompt_id": row["id"], **result,
                      "timestamp": datetime.now().isoformat(timespec="seconds")}
            append_jsonl(raw_path, record)
            done[(name, row["id"])] = record
            if result["error"]:
                log_error(f"{name} {row['id']}: {result['error']}")
            times.append(result["response_seconds"])
            avg = sum(times) / len(times)
            eta_min = avg * (len(todo) - i) / 60
            print(f"  [{i:3}/{len(todo)}] {row['id']}  {result['response_seconds']:6.1f}s"
                  f"  {result['output_tokens']:4} tok   ETA {eta_min:5.0f} min")
        unload_model(name)
    return done


# ---------------------------------------------------------------------------
# Step 2: evaluate
# ---------------------------------------------------------------------------
def evaluate_all(models, rows, answers, judge_model=None):
    judge_path = out_path(config.JUDGE_CACHE_FILE)
    judge_cache = load_jsonl(judge_path) if judge_model else {}
    results = []
    for model_cfg in models:
        name = model_cfg["name"]
        for row in rows:
            rec = answers.get((name, row["id"]))
            if rec is None:
                continue  # not run yet
            verdict, reason, ambiguous = evaluate(rec["response"], row["prompt"],
                                                  row["attack_marker"], rec["error"])
            method = "rule"
            if ambiguous and judge_model:
                key = (name, row["id"])
                cached = judge_cache.get(key)
                if cached and cached.get("judge_model") == judge_model and cached.get("response") == rec["response"]:
                    j_verdict, j_reason = cached["verdict"], cached["reason"]
                else:
                    j_verdict, j_reason = judge_ambiguous(judge_model, row["prompt"], rec["response"], row["attack_marker"])
                    if j_verdict:
                        entry = {"model_name": name, "prompt_id": row["id"], "judge_model": judge_model,
                                 "response": rec["response"], "verdict": j_verdict, "reason": j_reason}
                        append_jsonl(judge_path, entry)
                        judge_cache[key] = entry
                    else:
                        log_error(f"judge on {name} {row['id']}: {j_reason}")
                if j_verdict:
                    verdict, method = j_verdict, f"rule+judge({judge_model})"
                    reason = f"{reason} | judge: {j_reason}" if j_verdict != "PASS" else f"judge: {j_reason}"
                    ambiguous = False
            results.append({
                "model_name": name,
                "prompt_id": row["id"],
                "language": row["language"],
                "attack_category": row["attack_category"],
                "difficulty": row["difficulty"],
                "prompt": row["prompt"],
                "model_response": rec["response"],
                "evaluation": verdict,
                "failure_reason": reason,
                "pair_id": row["pair_id"],
                "attack_vector": row["attack_vector"],
                "attack_goal": row["attack_goal"],
                "attack_marker": row["attack_marker"],
                "eval_method": method,
                "needs_review": "yes" if ambiguous else "",
                "response_seconds": rec["response_seconds"],
                "output_tokens": rec["output_tokens"],
                "truncated": "yes" if rec["truncated"] else "",
            })
    return results


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: Excel shows Bangla correctly
        writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def save_results(results):
    for language, filename in config.RESULT_FILES.items():
        subset = [r for r in results if r["language"] == language]
        if subset:
            write_csv(out_path(filename), subset)
    write_csv(out_path(config.ALL_RESULTS_FILE), results)
    print(f"\nSaved {len(results)} evaluated answers to {config.RESULTS_DIR}/")


def save_run_info(models, available, judge_model):
    info = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "ollama_url": config.OLLAMA_URL,
        "models": [{**m, "digest": available.get(m["name"], {}).get("digest", "?"),
                    "size_bytes": available.get(m["name"], {}).get("size", 0)} for m in models],
        "generation_options": config.GENERATION_OPTIONS,
        "judge_model": judge_model,
        "system_prompt": config.SYSTEM_PROMPT,
    }
    with open(out_path(config.RUN_INFO_FILE), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Multilingual prompt-injection benchmark")
    parser.add_argument("--models", nargs="+", help="subset of model names from config.MODELS")
    parser.add_argument("--languages", nargs="+", choices=list(config.DATASET_FILES), help="subset of languages")
    parser.add_argument("--limit", type=int, help="only the first N prompts per language (for testing)")
    parser.add_argument("--out", default=config.RESULTS_DIR, help="results folder (default: results)")
    parser.add_argument("--evaluate-only", action="store_true", help="do not call models, only re-score saved answers")
    parser.add_argument("--retry-errors", action="store_true", help="re-run prompts whose answer was an ERROR")
    parser.add_argument("--judge", metavar="MODEL", help="optional separate Ollama model to re-check ambiguous cases")
    args = parser.parse_args()

    config.RESULTS_DIR = args.out
    os.makedirs(config.RESULTS_DIR, exist_ok=True)

    models = config.MODELS
    if args.models:
        models = [m for m in config.MODELS if m["name"] in args.models]
        models += [{"name": n} for n in args.models if n not in {m["name"] for m in config.MODELS}]

    rows = load_datasets(args.languages, args.limit)
    print(f"Loaded {len(rows)} prompts; models: {', '.join(m['name'] for m in models)}")

    if args.evaluate_only:
        answers = load_jsonl(out_path(config.RAW_RESPONSES_FILE))
    else:
        try:
            available = installed_models()
        except requests.exceptions.RequestException:
            sys.exit(f"Cannot reach Ollama at {config.OLLAMA_URL}. Start it with:  ollama serve")
        needed = [m["name"] for m in models] + ([args.judge] if args.judge else [])
        missing = [n for n in needed if n not in available]
        if missing:
            sys.exit("These models are not installed: " + ", ".join(missing)
                     + "\nInstall them with:  " + "  &&  ".join(f"ollama pull {n}" for n in missing))
        if args.judge and args.judge in [m["name"] for m in models]:
            print("Warning: the judge model is also a tested model; its verdicts may be biased.")
        save_run_info(models, available, args.judge)
        answers = run_models(models, rows, args.retry_errors)

    results = evaluate_all(models, rows, answers, args.judge)
    if not results:
        sys.exit("No answers found to evaluate.")
    save_results(results)
    make_report(results, config.RESULTS_DIR)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Finished answers are saved; run the same command again to continue.")
