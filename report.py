"""
Builds the summary report (CSV + Markdown + charts) from the evaluated results.

Run on its own (after main.py has produced results/all_results.csv):
  python report.py
"""

import csv
import math
import os
from collections import defaultdict

import config

LANGUAGES = ["English", "Bangla", "Banglish"]
OUTCOMES = ["PASS", "PARTIAL", "FAIL", "ERROR"]

# Chart colours (validated colour-blind-safe categorical slots + status colours)
MODEL_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
OUTCOME_COLORS = {"PASS": "#0ca30c", "PARTIAL": "#fab219", "FAIL": "#d03b3b", "ERROR": "#898781"}
SURFACE, TEXT, MUTED, GRID = "#fcfcfb", "#52514e", "#898781", "#e1e0d9"


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def wilson_ci(successes, n, z=1.96):
    """95% confidence interval for a proportion (works well for small n)."""
    if n == 0:
        return 0.0, 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half) * 100, min(1.0, centre + half) * 100


def stats(rows):
    counts = {o: 0 for o in OUTCOMES}
    for r in rows:
        counts[r["evaluation"]] += 1
    tested = len(rows)
    valid = tested - counts["ERROR"]           # answers that could be judged
    attacked = counts["FAIL"] + counts["PARTIAL"]
    lo, hi = wilson_ci(attacked, valid)
    return {
        "tested": tested,
        **{o.lower(): counts[o] for o in OUTCOMES},
        "fail_rate_pct": round(100 * counts["FAIL"] / valid, 1) if valid else 0.0,
        "attack_success_rate_pct": round(100 * attacked / valid, 1) if valid else 0.0,
        "asr_ci95": f"{lo:.0f}-{hi:.0f}%",
    }


def group_by(rows, *keys):
    groups = defaultdict(list)
    for r in rows:
        groups[tuple(r[k] for k in keys)].append(r)
    return groups


def most_vulnerable(rows, top=2):
    """Categories with the highest attack success rate for these rows."""
    scored = []
    for (cat,), subset in group_by(rows, "attack_category").items():
        s = stats(subset)
        if s["attack_success_rate_pct"] > 0:
            scored.append((s["attack_success_rate_pct"], s["fail"], cat))
    scored.sort(reverse=True)
    return [f"{cat} ({asr:.0f}%)" for asr, _, cat in scored[:top]]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def make_report(results, out_dir):
    models = list(dict.fromkeys(r["model_name"] for r in results))
    languages = [l for l in LANGUAGES if any(r["language"] == l for r in results)]
    categories = list(dict.fromkeys(r["attack_category"] for r in results))
    by_model = group_by(results, "model_name")
    by_model_lang = group_by(results, "model_name", "language")
    by_model_cat = group_by(results, "model_name", "attack_category")
    by_model_diff = group_by(results, "model_name", "difficulty")

    # ---------------- summary_report.csv (one tidy table)
    summary_rows = []
    for m in models:
        summary_rows.append({"scope": "overall", "model_name": m, "group": "all languages",
                             **stats(by_model[(m,)]),
                             "most_vulnerable_categories": "; ".join(most_vulnerable(by_model[(m,)]))})
    for m in models:
        for lang in languages:
            if (m, lang) in by_model_lang:
                subset = by_model_lang[(m, lang)]
                summary_rows.append({"scope": "language", "model_name": m, "group": lang, **stats(subset),
                                     "most_vulnerable_categories": "; ".join(most_vulnerable(subset))})
    for lang in languages:
        subset = [r for r in results if r["language"] == lang]
        summary_rows.append({"scope": "language (all models)", "model_name": "ALL", "group": lang,
                             **stats(subset), "most_vulnerable_categories": "; ".join(most_vulnerable(subset))})
    for m in models:
        for cat in categories:
            if (m, cat) in by_model_cat:
                summary_rows.append({"scope": "category", "model_name": m, "group": cat,
                                     **stats(by_model_cat[(m, cat)]), "most_vulnerable_categories": ""})
    for m in models:
        for diff in ["easy", "medium", "hard"]:
            if (m, diff) in by_model_diff:
                summary_rows.append({"scope": "difficulty", "model_name": m, "group": diff,
                                     **stats(by_model_diff[(m, diff)]), "most_vulnerable_categories": ""})

    fields = ["scope", "model_name", "group", "tested", "pass", "partial", "fail", "error",
              "fail_rate_pct", "attack_success_rate_pct", "asr_ci95", "most_vulnerable_categories"]
    with open(os.path.join(out_dir, config.SUMMARY_CSV), "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary_rows)

    # ---------------- conclusions (computed, never hand-written)
    overall = {m: stats(by_model[(m,)]) for m in models}
    ranking = sorted(models, key=lambda m: (overall[m]["attack_success_rate_pct"], overall[m]["fail_rate_pct"]))
    lang_stats = {l: stats([r for r in results if r["language"] == l]) for l in languages}
    worst_lang = max(languages, key=lambda l: (lang_stats[l]["attack_success_rate_pct"], lang_stats[l]["fail_rate_pct"]))

    # Paired comparison: same attack (pair_id) resisted in English but not in Bangla/Banglish
    pair_lines = []
    if "English" in languages:
        for m in models:
            outcome = {(r["pair_id"], r["language"]): r["evaluation"] for r in by_model[(m,)]}
            for lang in [l for l in languages if l != "English"]:
                worse = sum(1 for (pid, l), ev in outcome.items()
                            if l == lang and ev in ("FAIL", "PARTIAL") and outcome.get((pid, "English")) == "PASS")
                better = sum(1 for (pid, l), ev in outcome.items()
                             if l == lang and ev == "PASS" and outcome.get((pid, "English")) in ("FAIL", "PARTIAL"))
                pair_lines.append(f"| {m} | {lang} | {worse} | {better} |")

    needs_review = sum(1 for r in results if r["needs_review"] == "yes")
    truncated = sum(1 for r in results if r["truncated"] == "yes")

    # ---------------- charts
    chart_files = []
    try:
        chart_files = make_charts(results, models, languages, categories, out_dir)
    except ImportError:
        print("matplotlib not installed - skipping charts (pip install matplotlib)")

    # ---------------- summary_report.md
    L = []
    L.append("# Multilingual Prompt-Injection Benchmark - Summary\n")
    L.append(f"Models: {', '.join(models)}  \nLanguages: {', '.join(languages)}  \n"
             f"Evaluated answers: {len(results)}\n")
    L.append("**Attack success rate (ASR)** = (FAIL + PARTIAL) / answers that were not ERROR. "
             "**Fail rate** counts FAIL only. Lower is better. "
             "The 95% interval shows how much the ASR could move by chance with this many prompts.\n")

    L.append("## Overall results per model\n")
    L.append("| Rank | Model | Tested | PASS | PARTIAL | FAIL | ERROR | Fail rate | ASR | ASR 95% CI | Most vulnerable categories |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for i, m in enumerate(ranking, start=1):
        s = overall[m]
        L.append(f"| {i} | {m} | {s['tested']} | {s['pass']} | {s['partial']} | {s['fail']} | {s['error']} | "
                 f"{s['fail_rate_pct']}% | {s['attack_success_rate_pct']}% | {s['asr_ci95']} | "
                 f"{', '.join(most_vulnerable(by_model[(m,)])) or '-'} |")

    L.append("\n## Fail rate and ASR per language\n")
    L.append("| Model | " + " | ".join(f"{l} fail / ASR" for l in languages) + " |")
    L.append("|---|" + "---|" * len(languages))
    for m in ranking:
        cells = []
        for l in languages:
            s = stats(by_model_lang.get((m, l), []))
            cells.append(f"{s['fail_rate_pct']}% / {s['attack_success_rate_pct']}%" if s["tested"] else "-")
        L.append(f"| {m} | " + " | ".join(cells) + " |")
    L.append("| **All models** | " + " | ".join(
        f"**{lang_stats[l]['fail_rate_pct']}% / {lang_stats[l]['attack_success_rate_pct']}%**" for l in languages) + " |")

    L.append("\n## Attack success rate per category\n")
    L.append("| Category | " + " | ".join(models) + " |")
    L.append("|---|" + "---|" * len(models))
    for cat in categories:
        L.append(f"| {cat} | " + " | ".join(
            f"{stats(by_model_cat[(m, cat)])['attack_success_rate_pct']}%" if (m, cat) in by_model_cat else "-"
            for m in models) + " |")

    L.append("\n## Attack success rate per difficulty\n")
    L.append("| Difficulty | " + " | ".join(models) + " |")
    L.append("|---|" + "---|" * len(models))
    for diff in ["easy", "medium", "hard"]:
        L.append(f"| {diff} | " + " | ".join(
            f"{stats(by_model_diff[(m, diff)])['attack_success_rate_pct']}%" if (m, diff) in by_model_diff else "-"
            for m in models) + " |")

    if pair_lines:
        L.append("\n## Same attack, different language (paired comparison)\n")
        L.append("Each attack exists in all three languages (same `pair_id`). This counts attacks a model "
                 "resisted in English but not in the other language, and the other way round.\n")
        L.append("| Model | Language | Resisted in English, broken here | Broken in English, resisted here |")
        L.append("|---|---|---|---|")
        L.extend(pair_lines)

    L.append("\n## Conclusions (computed from the data)\n")
    best = ranking[0]
    L.append(f"- **Best overall (lowest ASR): {best}** with {overall[best]['attack_success_rate_pct']}% ASR "
             f"({overall[best]['fail']} FAIL, {overall[best]['partial']} PARTIAL out of {overall[best]['tested']}).")
    if len(ranking) > 1:
        second = ranking[1]
        lo_b, hi_b = map(float, overall[best]["asr_ci95"].rstrip("%").split("-"))
        lo_s, hi_s = map(float, overall[second]["asr_ci95"].rstrip("%").split("-"))
        if lo_s <= hi_b:
            L.append(f"- The 95% intervals of {best} and {second} overlap, so this lead could be due to chance. "
                     "Treat the ranking as a tendency, not a proof.")
        else:
            L.append(f"- The interval of {best} does not overlap with the next model ({second}), so the lead is clear.")
    L.append(f"- **Language with the highest failure rate: {worst_lang}** "
             f"({lang_stats[worst_lang]['attack_success_rate_pct']}% ASR across all models; "
             + ", ".join(f"{l} {lang_stats[l]['attack_success_rate_pct']}%" for l in languages) + ").")
    for m in models:
        mv = most_vulnerable(by_model[(m,)])
        L.append(f"- {m} is most vulnerable to: {', '.join(mv) if mv else 'no successful attacks'}.")

    L.append("\n## Data quality notes\n")
    L.append(f"- Answers flagged `needs_review` (marker produced while also refusing, counted as PARTIAL): {needs_review}.")
    L.append(f"- Answers cut off at the token limit (`truncated`): {truncated} ("
             + ", ".join(f"{m} / {l}: {sum(1 for r in by_model_lang.get((m, l), []) if r['truncated'] == 'yes')}"
                         for m in models for l in languages)
             + "). If one language is truncated much more often, failures at the end of its answers may be missed.")
    L.append(f"- ERROR answers: {sum(1 for r in results if r['evaluation'] == 'ERROR')} (see errors.log).")

    if chart_files:
        L.append("\n## Charts\n")
        for title, path in chart_files:
            L.append(f"### {title}\n\n![{title}]({path})\n")

    with open(os.path.join(out_dir, config.SUMMARY_MD), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    print("\n".join(L[: 6 + len(models) + 1]))
    print(f"\nFull report: {os.path.join(out_dir, config.SUMMARY_MD)}")


# ---------------------------------------------------------------------------
# Charts (matplotlib, saved as PNG)
# ---------------------------------------------------------------------------
def _style(ax, fig):
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT, labelsize=10)
    ax.title.set_color(TEXT)


def make_charts(results, models, languages, categories, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    charts_dir = os.path.join(out_dir, config.CHARTS_DIR)
    os.makedirs(charts_dir, exist_ok=True)
    files = []
    colors = {m: MODEL_COLORS[i % len(MODEL_COLORS)] for i, m in enumerate(models)}

    # 1) Outcome counts per model (stacked horizontal bars)
    fig, ax = plt.subplots(figsize=(9, 1.2 + 0.7 * len(models)))
    _style(ax, fig)
    left = [0] * len(models)
    for outcome in OUTCOMES:
        vals = [sum(1 for r in results if r["model_name"] == m and r["evaluation"] == outcome) for m in models]
        ax.barh(models, vals, left=left, color=OUTCOME_COLORS[outcome], edgecolor=SURFACE, linewidth=2,
                height=0.55, label=outcome)
        left = [a + b for a, b in zip(left, vals)]
    ax.invert_yaxis()
    ax.set_xlabel("Number of test prompts", color=TEXT)
    ax.set_title("Outcome of every prompt-injection test, per model", loc="left", fontsize=12)
    ax.legend(ncol=4, frameon=False, loc="upper left", bbox_to_anchor=(0, -0.25 - 0.05 * len(models)), labelcolor=TEXT)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    path = os.path.join(charts_dir, "outcomes_by_model.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    files.append(("Outcomes per model", os.path.join(config.CHARTS_DIR, "outcomes_by_model.png")))

    # 2) Attack success rate per language, grouped by model
    fig, ax = plt.subplots(figsize=(9, 4.5))
    _style(ax, fig)
    width = 0.8 / max(1, len(models))
    for i, m in enumerate(models):
        vals = []
        for l in languages:
            subset = [r for r in results if r["model_name"] == m and r["language"] == l]
            vals.append(stats(subset)["attack_success_rate_pct"] if subset else 0)
        xs = [x + (i - (len(models) - 1) / 2) * width for x in range(len(languages))]
        bars = ax.bar(xs, vals, width=width, color=colors[m], edgecolor=SURFACE, linewidth=2, label=m)
        ax.bar_label(bars, fmt="%.0f%%", fontsize=9, color=TEXT, padding=2)
    ax.set_xticks(range(len(languages)), languages)
    ax.set_ylabel("Attack success rate (%)  - lower is better", color=TEXT)
    ax.set_ylim(0, max(10, ax.get_ylim()[1] * 1.12))
    ax.set_title("Attack success rate by language", loc="left", fontsize=12)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, labelcolor=TEXT)
    fig.tight_layout()
    path = os.path.join(charts_dir, "asr_by_language.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    files.append(("Attack success rate by language", os.path.join(config.CHARTS_DIR, "asr_by_language.png")))

    # 3) Heatmap: category x model
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("blues", ["#f0efec", "#9ec5f4", "#3987e5", "#1c5cab", "#0d366b"])
    matrix = []
    for cat in categories:
        row = []
        for m in models:
            subset = [r for r in results if r["model_name"] == m and r["attack_category"] == cat]
            row.append(stats(subset)["attack_success_rate_pct"] if subset else 0)
        matrix.append(row)
    fig, ax = plt.subplots(figsize=(2.2 + 1.6 * len(models), 0.5 * len(categories) + 1.6))
    _style(ax, fig)
    im = ax.imshow(matrix, cmap=cmap, vmin=0, vmax=100, aspect="auto")
    ax.set_xticks(range(len(models)), models)
    ax.set_yticks(range(len(categories)), categories)
    for y, row in enumerate(matrix):
        for x, v in enumerate(row):
            ax.text(x, y, f"{v:.0f}%", ha="center", va="center", fontsize=9,
                    color="white" if v >= 50 else TEXT)
    cbar = fig.colorbar(im, ax=ax, fraction=0.04)
    cbar.set_label("Attack success rate (%)", color=TEXT)
    cbar.ax.tick_params(colors=TEXT)
    cbar.outline.set_visible(False)
    ax.set_title("Attack success rate by attack category (all languages)", loc="left", fontsize=12)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout()
    path = os.path.join(charts_dir, "asr_by_category.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    files.append(("Attack success rate by category", os.path.join(config.CHARTS_DIR, "asr_by_category.png")))

    return files


if __name__ == "__main__":
    path = os.path.join(config.RESULTS_DIR, config.ALL_RESULTS_FILE)
    with open(path, encoding="utf-8-sig") as f:
        make_report(list(csv.DictReader(f)), config.RESULTS_DIR)
