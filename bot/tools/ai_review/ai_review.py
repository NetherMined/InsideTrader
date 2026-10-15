"""Nightly AI review of the hindsight baseline. Runs on the Windows host, not in Docker.

For every ./baseline/<day>/ without a report:
  1. copy summary.json, signals.csv, opportunities.csv into the sandbox inbox
  2. ask the default model (Muse Spark via OpenCode) for a JSON review, falling back to Ollama
  3. check every cited number and row against the baseline files
  4. re-test each suggested filter on up to 7 days of signals.csv (what-if)
  5. write reports/<day>.json + .md, and queue checked suggestions as `pending` in rule_patches

Only baseline statistics leave the machine — never the repo, .env or keys.

Usage:
  python bot/tools/ai_review/ai_review.py                   # review all pending days
  python bot/tools/ai_review/ai_review.py --day 2026-10-14  # one day (re-run)
  python bot/tools/ai_review/ai_review.py --bakeoff <day>   # every model on one day, scored
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BASELINE = REPO / "baseline"

DEFAULTS = {
    "AI_REVIEW_DIR": r"D:\Personal\InsideTrader-ai-review",
    "AI_REVIEW_PROVIDER": "opencode",
    "AI_REVIEW_MODEL": "opencode/muse-spark-1.3-contributor-free",
    "AI_REVIEW_FALLBACK": "ollama:gpt-oss:20b",
    "AI_REVIEW_BAKEOFF": "opencode:opencode/muse-spark-1.3-contributor-free,ollama:gpt-oss:20b",
    "OLLAMA_URL": "http://localhost:11434",
    "POSTGRES_CONTAINER": "insidetrader-postgres-1",
}
LOCAL_ROWS = 100
WHAT_IF_DAYS = 7
OPENCODE_TIMEOUT_S = 15 * 60
OLLAMA_TIMEOUT_S = 60 * 60
PROMPT = "Review the attached baseline files following AGENTS.md. Reply with the JSON object only."
FILTER_FIELDS = {"confidence": float, "atr_pct": float, "adx": float, "hour": int, "side": str, "research": str}
FILTER_OPS = {"<", "<=", ">", ">=", "==", "!=", "in", "not_in"}


def _config() -> dict:
    """Defaults, overridden by AI_REVIEW_* / OLLAMA_URL lines in the repo .env, then by the environment."""
    cfg = dict(DEFAULTS)
    env_file = REPO / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            key, _, val = line.strip().partition("=")
            if key in cfg and val:
                cfg[key] = val.strip()
    for key in cfg:
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    return cfg


def _log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def _read_csv(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)


def _sync_sandbox(review: Path) -> None:
    for sub in ("inbox", "reports"):
        (review / sub).mkdir(parents=True, exist_ok=True)
    for name in ("AGENTS.md", "opencode.json"):
        shutil.copyfile(HERE / name, review / name)


def _prepare_inbox(review: Path, day: str, trimmed: bool) -> list[Path]:
    """Copy one day's baseline into the sandbox. Local models get the top rows only."""
    src = BASELINE / day
    dst = review / "inbox" / (day + ("-trimmed" if trimmed else ""))
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src / "summary.json", dst / "summary.json")
    signals = _read_csv(src / "signals.csv")
    opps = _read_csv(src / "opportunities.csv")
    if trimmed:
        signals = sorted(signals, key=lambda r: -abs(float(r["sim_net_usdt"] or 0)))[:LOCAL_ROWS]
        opps = sorted(opps, key=lambda r: (-float(r["best_move_pct"] or 0), int(r["minutes_to_target"] or 999)))[:LOCAL_ROWS]
    _write_csv(dst / "signals.csv", signals)
    _write_csv(dst / "opportunities.csv", opps)
    return [dst / "summary.json", dst / "signals.csv", dst / "opportunities.csv"]


def _extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])


def _run_opencode(model: str, files: list[Path], review: Path) -> str:
    exe = shutil.which("opencode")
    if not exe:
        raise RuntimeError("opencode not found on PATH")
    # Prompt goes before -f: -f takes a list and would swallow it as a file name.
    cmd = [exe, "run", "--dir", str(review), "-m", model, PROMPT, "-f", *[str(f) for f in files]]
    proc = subprocess.run(cmd, cwd=review, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=OPENCODE_TIMEOUT_S)
    if proc.returncode != 0:
        raise RuntimeError(f"opencode exit {proc.returncode}: {proc.stderr.strip()[-400:]}")
    return proc.stdout


def _run_ollama(model: str, files: list[Path], review: Path, url: str) -> str:
    parts = [f"=== {f.name} ===\n{f.read_text(encoding='utf-8')}" for f in files]
    body = {
        "model": model,
        "stream": False,
        "format": "json",
        # Long reasoning can use up the context before the JSON starts; gpt-oss cannot turn it off
        **({} if model.startswith("gpt-oss") else {"think": False}),
        "options": {"num_ctx": 32768},
        "messages": [
            {"role": "system", "content": (review / "AGENTS.md").read_text(encoding="utf-8")},
            {"role": "user", "content": PROMPT + "\n\n" + "\n\n".join(parts)},
        ],
    }
    req = urllib.request.Request(f"{url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_S) as resp:
        data = json.loads(resp.read())
    content = data["message"].get("content") or ""
    if not content.strip():
        raise ValueError(f"empty reply (done_reason={data.get('done_reason')}, "
                         f"{data.get('eval_count')} tokens generated, "
                         f"{len(data['message'].get('thinking') or '')} chars of thinking)")
    return content


def _ask(spec: str, day: str, review: Path, cfg: dict) -> tuple[dict, float]:
    """spec is 'opencode:<model>' or 'ollama:<model>'. Returns (report, seconds)."""
    provider, _, model = spec.partition(":")
    t0 = time.time()
    if provider == "opencode":
        text = _run_opencode(model, _prepare_inbox(review, day, trimmed=False), review)
    elif provider == "ollama":
        text = _run_ollama(model, _prepare_inbox(review, day, trimmed=True), review, cfg["OLLAMA_URL"])
    else:
        raise ValueError(f"unknown provider {provider!r}")
    try:
        report = _extract_json(text)
    except ValueError:
        raw = review / "reports" / f"{day}_{spec.replace(':', '_').replace('/', '_')}_raw.txt"
        raw.write_text(text, encoding="utf-8")
        raise ValueError(f"reply was not valid JSON ({len(text)} chars, saved to {raw.name})")
    report["_model"] = spec
    return report, round(time.time() - t0, 1)


def _match(row: dict, flt: dict) -> bool:
    field, op, value = flt["field"], flt["op"], flt["value"]
    cast = FILTER_FIELDS[field]
    raw = row.get(field)
    if raw in (None, ""):
        return False
    v = cast(raw)
    if op in ("in", "not_in"):
        values = {cast(x) for x in (value if isinstance(value, list) else [value])}
        return (v in values) == (op == "in")
    value = cast(value)
    return {"<": v < value, "<=": v <= value, ">": v > value, ">=": v >= value,
            "==": v == value, "!=": v != value}[op]


def _what_if(flt: dict, mode: str, day: str) -> dict:
    """Apply a filter to the last WHAT_IF_DAYS days of signals.csv and compare simulated P&L."""
    days = sorted(d.name for d in BASELINE.iterdir() if (d / "signals.csv").exists() and d.name <= day)[-WHAT_IF_DAYS:]
    rows = [r for d in days for r in _read_csv(BASELINE / d / "signals.csv") if r.get("above_threshold") == "True"]
    hit = [r for r in rows if _match(r, flt)]
    kept = [r for r in rows if r not in hit] if mode == "skip" else hit

    def net(rs):
        return round(sum(float(r["sim_net_usdt"]) for r in rs), 3)

    return {
        "days": days, "signals_before": len(rows), "signals_after": len(kept),
        "net_before": net(rows), "net_after": net(kept),
        "avg_before": round(net(rows) / len(rows), 4) if rows else None,
        "avg_after": round(net(kept) / len(kept), 4) if kept else None,
    }


def _verify(report: dict, day: str) -> dict:
    summary = json.loads((BASELINE / day / "summary.json").read_text(encoding="utf-8"))
    opp_keys = {(r["symbol"], r["time"], r["side"]) for r in _read_csv(BASELINE / day / "opportunities.csv")}
    checks = {"patterns_ok": 0, "patterns_bad": [], "trades_ok": 0, "trades_bad": [], "suggestions": []}

    for p in report.get("patterns", []) or []:
        g = summary.get("groups", {}).get(str(p.get("group")), {}).get(str(p.get("bucket")))
        ok = bool(g) and g.get("n") == p.get("n") and abs((g.get("sim_net_avg") or 0) - float(p.get("sim_net_avg") or 0)) < 0.001
        if ok and p.get("profit_factor") is not None and g.get("profit_factor") is not None:
            ok = abs(g["profit_factor"] - float(p["profit_factor"])) < 0.015
        if ok:
            checks["patterns_ok"] += 1
        else:
            checks["patterns_bad"].append({"group": p.get("group"), "bucket": p.get("bucket"), "claimed_n": p.get("n"),
                                           "actual": g or "no such group/bucket"})

    for t in report.get("baseline_trades", []) or []:
        key = (str(t.get("symbol")), str(t.get("time")), str(t.get("side")))
        if key in opp_keys:
            checks["trades_ok"] += 1
        else:
            checks["trades_bad"].append(list(key))

    for s in report.get("rule_suggestions", []) or []:
        flt, mode = s.get("filter") or {}, s.get("keep_or_skip", "skip")
        entry = {"change": s.get("change"), "filter": flt, "mode": mode}
        if flt.get("field") not in FILTER_FIELDS or flt.get("op") not in FILTER_OPS or mode not in ("skip", "keep"):
            entry["valid"] = False
        else:
            try:
                entry["what_if"] = _what_if(flt, mode, day)
                wi = entry["what_if"]
                entry["valid"] = True
                entry["improves"] = (wi["avg_after"] is not None and wi["avg_before"] is not None
                                     and wi["avg_after"] > wi["avg_before"] and wi["signals_after"] >= 30)
            except (ValueError, TypeError, KeyError) as e:
                entry["valid"], entry["error"] = False, str(e)
        checks["suggestions"].append(entry)

    cited = len(report.get("patterns", []) or []) + len(report.get("baseline_trades", []) or [])
    checks["accuracy"] = round((checks["patterns_ok"] + checks["trades_ok"]) / cited, 3) if cited else 0.0
    checks["valid_suggestions"] = sum(1 for s in checks["suggestions"] if s.get("valid"))
    return checks


def _to_markdown(day: str, report: dict, checks: dict, seconds: float) -> str:
    lines = [f"# AI review — {day}", "",
             f"- Model: `{report['_model']}` ({seconds}s)",
             f"- Verification: {checks['accuracy']:.0%} of cited patterns/rows match the baseline "
             f"({checks['patterns_ok']} patterns, {checks['trades_ok']} trades OK)", "",
             f"**{report.get('headline', '')}**", "", "## Patterns", "",
             "| Group | Bucket | n | avg %/trade | PF | Finding | Conf |", "|---|---|---|---|---|---|---|"]
    for p in report.get("patterns", []) or []:
        lines.append(f"| {p.get('group')} | {p.get('bucket')} | {p.get('n')} | {p.get('sim_net_avg')} | "
                     f"{p.get('profit_factor')} | {p.get('finding')} | {p.get('confidence')} |")
    lines += ["", "## Suggestions (what-if on last days, above-threshold signals)", ""]
    for s in checks["suggestions"]:
        wi = s.get("what_if") or {}
        verdict = "improves" if s.get("improves") else ("invalid" if not s.get("valid") else "does not improve")
        lines.append(f"- **{s.get('change')}** — `{json.dumps(s.get('filter'))}` ({s.get('mode')}): {verdict}. "
                     f"avg {wi.get('avg_before')} → {wi.get('avg_after')} %/trade, "
                     f"{wi.get('signals_before')} → {wi.get('signals_after')} signals over {len(wi.get('days', []))} day(s)")
    lines += ["", "## Baseline trades", "", "| Symbol | Time | Side | Min to target | Best move % | Coverage |",
              "|---|---|---|---|---|---|"]
    for t in report.get("baseline_trades", []) or []:
        lines.append(f"| {t.get('symbol')} | {t.get('time')} | {t.get('side')} | {t.get('minutes_to_target')} | "
                     f"{t.get('best_move_pct')} | {t.get('coverage')} |")
    lines += ["", "## Missed opportunities", "", str(report.get("missed_opportunity_notes", ""))]
    if checks["patterns_bad"] or checks["trades_bad"]:
        lines += ["", "## Failed checks", "", "```", json.dumps(
            {"patterns": checks["patterns_bad"], "trades": checks["trades_bad"]}, indent=2, ensure_ascii=False), "```"]
    return "\n".join(lines) + "\n"


def _queue_patches(day: str, report: dict, checks: dict, cfg: dict) -> int:
    """Insert suggestions that passed the what-if as `pending` rule_patches. Nothing applies them automatically."""
    queued = 0
    for s in checks["suggestions"]:
        if not s.get("improves"):
            continue
        wi = s["what_if"]
        labels = json.dumps({"source": "ai_review", "day": day, "model": report["_model"],
                             "filter": s["filter"], "mode": s["mode"], "what_if": wi}, ensure_ascii=False)
        because = (f"AI review {day}: avg {wi['avg_before']} -> {wi['avg_after']} %/trade over "
                   f"{len(wi['days'])} day(s), {wi['signals_before']} -> {wi['signals_after']} signals")
        sql = ("INSERT INTO rule_patches (from_version, change, because, scope, labels, status) "
               "VALUES (0, :'change', :'because', 'ai_review', CAST(:'labels' AS json), 'pending');\n")
        proc = subprocess.run(
            ["docker", "exec", "-i", cfg["POSTGRES_CONTAINER"], "psql", "-U", "insidetrader", "-d", "insidetrader",
             "-v", "ON_ERROR_STOP=1", "-v", f"change={s['change']}", "-v", f"because={because}", "-v", f"labels={labels}",
             "-f", "-"],
            input=sql, capture_output=True, text=True, encoding="utf-8",
        )
        if proc.returncode == 0:
            queued += 1
        else:
            _log(f"  could not queue patch: {proc.stderr.strip()[-300:]}")
    return queued


def review_day(day: str, cfg: dict, review: Path) -> Path | None:
    specs = [f"{cfg['AI_REVIEW_PROVIDER']}:{cfg['AI_REVIEW_MODEL']}", cfg["AI_REVIEW_FALLBACK"]]
    for spec in specs:
        try:
            _log(f"{day}: asking {spec}")
            report, seconds = _ask(spec, day, review, cfg)
        except Exception as e:
            _log(f"{day}: {spec} failed — {e}")
            continue
        checks = _verify(report, day)
        out = review / "reports" / day
        (out.parent / f"{day}.json").write_text(json.dumps(
            {"report": report, "verification": checks, "seconds": seconds,
             "reviewed_at": datetime.now(timezone.utc).isoformat()}, indent=2, ensure_ascii=False), encoding="utf-8")
        (out.parent / f"{day}.md").write_text(_to_markdown(day, report, checks, seconds), encoding="utf-8")
        queued = _queue_patches(day, report, checks, cfg)
        _log(f"{day}: done with {spec} in {seconds}s — accuracy {checks['accuracy']:.0%}, "
             f"{checks['valid_suggestions']} valid suggestion(s), {queued} queued as pending")
        return out.parent / f"{day}.md"
    _log(f"{day}: all models failed")
    return None


def bakeoff(day: str, cfg: dict, review: Path) -> None:
    out = review / "reports" / "bakeoff"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for spec in cfg["AI_REVIEW_BAKEOFF"].split(","):
        spec = spec.strip()
        _log(f"bake-off {day}: {spec}")
        try:
            report, seconds = _ask(spec, day, review, cfg)
            checks = _verify(report, day)
            rows.append({"model": spec, "json": "valid", "seconds": seconds, "accuracy": checks["accuracy"],
                         "patterns_ok": f"{checks['patterns_ok']}/{len(report.get('patterns') or [])}",
                         "trades_ok": f"{checks['trades_ok']}/{len(report.get('baseline_trades') or [])}",
                         "valid_suggestions": f"{checks['valid_suggestions']}/{len(report.get('rule_suggestions') or [])}"})
            safe = spec.replace(":", "_").replace("/", "_")
            (out / f"{day}_{safe}.json").write_text(json.dumps({"report": report, "verification": checks},
                                                               indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            _log(f"bake-off {day}: {spec} failed — {e}")
            rows.append({"model": spec, "json": f"failed: {str(e)[:120]}", "seconds": None, "accuracy": 0.0,
                         "patterns_ok": "-", "trades_ok": "-", "valid_suggestions": "-"})
    # Merge with earlier runs on the same day, so models can be scored one at a time
    scores_file = out / f"{day}_scores.json"
    if scores_file.exists():
        new = {r["model"] for r in rows}
        rows += [r for r in json.loads(scores_file.read_text(encoding="utf-8")) if r["model"] not in new]
    rows.sort(key=lambda r: -r["accuracy"])
    scores_file.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", help="review one baseline folder (re-runs even if reported)")
    ap.add_argument("--bakeoff", metavar="DAY", help="run every AI_REVIEW_BAKEOFF model on DAY and score them")
    a = ap.parse_args()
    cfg = _config()
    review = Path(cfg["AI_REVIEW_DIR"])
    _sync_sandbox(review)
    if a.bakeoff:
        bakeoff(a.bakeoff, cfg, review)
        return
    if a.day:
        days = [a.day]
    else:
        days = sorted(d.name for d in BASELINE.iterdir()
                      if (d / "summary.json").exists() and not (review / "reports" / f"{d.name}.json").exists())
    if not days:
        _log("nothing to review")
    for day in days:
        review_day(day, cfg, review)


if __name__ == "__main__":
    sys.exit(main())
