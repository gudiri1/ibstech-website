from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(REPO_ROOT, "data")

TOOLS_JSON = os.path.join(DATA_DIR, "toolradar_tools.json")
META_JSON = os.path.join(DATA_DIR, "toolradar_meta.json")
SIGNALS_JSON = os.path.join(DATA_DIR, "toolradar_signals.json")

GITHUB_API = "https://api.github.com/search/repositories"

DIMENSIONS = [
    "Maturity",
    "Integration",
    "EmbeddedFit",
    "AuditTraceability",
    "AIValue",
    "TCO",
    "CommunitySupport",
]

PHASE_KEYWORDS: List[Tuple[str, List[str]]] = [
    ("Stakeholder Needs / System Requirements", ["requirements", "alm", "traceability", "reqif"]),
    ("Architektur/Design", ["mbse", "sysml", "model-based", "architecture", "simulation"]),
    ("Implementierung", ["static analysis", "linter", "code generation", "codegen", "compiler", "ide"]),
    ("Integration & Test (Komponenten)", ["unit test", "ci", "cd", "pipeline", "coverage", "testing"]),
    ("System Integration & Validierung", ["hil", "sil", "simulation", "gazebo", "isaac", "validation"]),
    ("Release / Operations", ["observability", "monitoring", "prometheus", "grafana", "deployment"]),
    ("Safety & Security", ["iso 26262", "iec 62304", "safety", "security", "vulnerability", "sast"]),
    ("Data/AI Lifecycle", ["mlops", "model registry", "drift", "training", "inference", "feature store"]),
    ("Documentation & Audit", ["doxygen", "sphinx", "audit", "traceability", "documentation"]),
]

EMBEDDED_HINTS = ["embedded", "mcu", "rtos", "zephyr", "freertos", "arm", "stm32", "bare-metal", "firmware"]
INTEGRATION_HINTS = ["github actions", "jira", "gitlab", "jenkins", "docker", "kubernetes", "ros", "ros2", "cmake"]
AUDIT_HINTS = ["trace", "audit", "compliance", "iso", "iec", "safety", "governance"]
AI_HINTS_ASSISTED = ["llm", "genai", "copilot", "agent", "ai-powered", "assistant"]
AI_HINTS_CORE = ["training", "inference", "mlops", "model registry", "drift", "synthetic data"]

def now_utc_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())

def gh_headers() -> Dict[str, str]:
    token = os.getenv("GITHUB_TOKEN")
    h = {"Accept": "application/vnd.github+json", "User-Agent": "toolradar-bot"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h

def http_get_json(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers=gh_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read().decode("utf-8")
        return json.loads(data)

def http_head_ok(url: str) -> Tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "toolradar-bot"}, method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = getattr(resp, "status", 200)
            return (200 <= code < 400), str(code)
    except Exception as e:
        return False, str(e)

def infer_vmodel_phases(text: str) -> List[str]:
    t = text.lower()
    phases: List[str] = []
    for phase, keys in PHASE_KEYWORDS:
        if any(k in t for k in keys):
            phases.append(phase)
    return phases or ["Data/AI Lifecycle"]

def infer_ai_involvement(text: str) -> str:
    t = text.lower()
    if any(k in t for k in AI_HINTS_CORE):
        return "core"
    if any(k in t for k in AI_HINTS_ASSISTED):
        return "assisted"
    return "none"

def clamp(n: int, lo: int = 0, hi: int = 5) -> int:
    return max(lo, min(hi, n))

def days_since(iso: str) -> int:
    try:
        d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return max(0, int((dt.datetime.now(dt.timezone.utc) - d).total_seconds() // 86400))
    except Exception:
        return 99999

def years_since(iso: str) -> float:
    try:
        d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return max(0.0, (dt.datetime.now(dt.timezone.utc) - d).days / 365.25)
    except Exception:
        return 0.0

def score_maturity(stars: int, age_years: float, updated_days: int) -> int:
    if stars >= 5000: s = 5
    elif stars >= 1000: s = 4
    elif stars >= 200: s = 3
    elif stars >= 50: s = 2
    else: s = 1
    if age_years >= 5: s += 1
    if updated_days > 365: s -= 2
    elif updated_days > 180: s -= 1
    elif updated_days < 30: s += 1
    return clamp(s, 0, 5)

def score_community(stars: int, forks: int, open_issues: int) -> int:
    base = 1
    if stars >= 5000: base = 5
    elif stars >= 1000: base = 4
    elif stars >= 200: base = 3
    elif stars >= 50: base = 2
    # forks helfen, zu viele offene Issues sind ein leichtes Risiko
    if forks >= 500: base += 1
    elif forks >= 100: base += 0
    if open_issues >= 500: base -= 1
    return clamp(base, 0, 5)

def score_embedded(text: str) -> int:
    t = text.lower()
    hits = sum(1 for k in EMBEDDED_HINTS if k in t)
    if hits >= 4: return 5
    if hits == 3: return 4
    if hits == 2: return 3
    if hits == 1: return 2
    return 1

def score_integration(text: str) -> int:
    t = text.lower()
    hits = sum(1 for k in INTEGRATION_HINTS if k in t)
    if hits >= 4: return 5
    if hits == 3: return 4
    if hits == 2: return 3
    if hits == 1: return 2
    return 1

def score_audit(text: str) -> int:
    t = text.lower()
    hits = sum(1 for k in AUDIT_HINTS if k in t)
    if hits >= 4: return 5
    if hits == 3: return 4
    if hits == 2: return 3
    if hits == 1: return 2
    return 1

def ai_value(ai_involvement: str) -> int:
    return {"none": 1, "assisted": 3, "core": 5}.get(ai_involvement, 2)

def classify(scores: Dict[str, int]) -> str:
    # einfache Klassifikation (heuristisch)
    m = scores.get("Maturity", 0)
    a = scores.get("AIValue", 0)
    if m >= 4 and a >= 3: return "Adopt"
    if m >= 3 and a >= 3: return "Trial"
    if m >= 2: return "Assess"
    return "Hold"

def build_queries() -> List[str]:
    return [
        "embedded rtos tool",
        "embedded static analysis",
        "robotics simulation tool",
        "hil sil framework",
        "mlops tool open source",
        "requirements traceability open source",
        "prometheus exporter embedded",
        "doxygen sphinx documentation tool",
    ]

def scout_github(max_results: int) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for q in build_queries():
        params = {
            "q": f"{q} in:name,description",
            "sort": "updated",
            "order": "desc",
            "per_page": 25,
        }
        url = GITHUB_API + "?" + urllib.parse.urlencode(params)
        data = http_get_json(url)
        items = data.get("items", [])

        for it in items:
            name = it.get("name") or "Unknown"
            full_name = it.get("full_name") or name
            html_url = it.get("html_url") or ""
            homepage = it.get("homepage") or ""
            desc = it.get("description") or ""

            created = it.get("created_at") or ""
            pushed = it.get("pushed_at") or ""
            stars = int(it.get("stargazers_count") or 0)
            forks = int(it.get("forks_count") or 0)
            open_issues = int(it.get("open_issues_count") or 0)

            blob = f"{full_name} {desc}"
            phases = infer_vmodel_phases(blob)
            ai = infer_ai_involvement(blob)

            updated_days = days_since(pushed) if pushed else 99999
            age_years = years_since(created) if created else 0.0

            scores = {
                "Maturity": score_maturity(stars, age_years, updated_days),
                "Integration": score_integration(blob),
                "EmbeddedFit": score_embedded(blob),
                "AuditTraceability": score_audit(blob),
                "AIValue": ai_value(ai),
                "TCO": 5,  # Open Source-only
                "CommunitySupport": score_community(stars, forks, open_issues),
            }
            classification = classify(scores)

            evidence = [html_url, homepage or html_url]
            candidates.append({
                "ToolName": name,
                "VendorOrganisation": (it.get("owner", {}) or {}).get("login", "Open Source"),
                "ToolType": "Open Source",
                "PrimaryUse": desc or "Open-source tool (discovered via GitHub search)",
                "VModelPhases": phases,
                "AIInvolvement": ai,
                "Classification": classification,
                "EvidenceLinks": evidence[:2],
                "LastVerifiedDate": dt.date.today().isoformat(),
                "UpdateSignal": ["GitHubReleases"],
                "RiskFlags": ["Unvalidated (auto-discovered)"],
                "Notes": f"Discovered via GitHub search query: '{q}'. Scores are heuristic.",
                "Repo": {
                    "full_name": full_name,
                    "url": html_url,
                    "stars": stars,
                    "forks": forks,
                    "open_issues": open_issues,
                    "pushed_at": pushed,
                    "created_at": created,
                },
                "Scores": scores,
            })

            if len(candidates) >= max_results * 3:
                break
        if len(candidates) >= max_results * 3:
            break

    # Dedup by ToolName
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for c in candidates:
        k = normalize_name(c["ToolName"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)
        if len(uniq) >= max_results:
            break
    uniq.sort(key=lambda x: (x.get("ToolName") or "").lower())
    return uniq

def merge_tools(existing: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int, List[Dict[str, Any]]]:
    idx = {normalize_name(t.get("ToolName", "")): t for t in existing if t.get("ToolName")}
    inserted = 0
    inserted_items: List[Dict[str, Any]] = []

    for c in new:
        k = normalize_name(c.get("ToolName", ""))
        if not k or k in idx:
            continue
        idx[k] = c
        inserted += 1
        inserted_items.append(c)

    merged = list(idx.values())
    merged.sort(key=lambda x: (x.get("ToolName") or "").lower())
    return merged, inserted, inserted_items

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=60)
    args = ap.parse_args()

    existing = load_json(TOOLS_JSON, [])
    if not isinstance(existing, list):
        existing = []

    run_ts = now_utc_iso()
    sha = os.getenv("GITHUB_SHA")

    try:
        discovered = scout_github(max_results=args.max)
        merged, inserted, inserted_items = merge_tools(existing, discovered)

        # Validator: check first evidence link for each tool (limit time)
        broken: List[Dict[str, Any]] = []
        for t in merged[:80]:
            links = t.get("EvidenceLinks") or []
            if not links:
                continue
            ok, info = http_head_ok(links[0])
            if not ok:
                broken.append({"tool": t.get("ToolName"), "link": links[0], "info": info})

        save_json(TOOLS_JSON, merged)
        save_json(META_JSON, {"lastRunUtc": run_ts, "lastRunCommit": sha, "toolsCount": len(merged)})

        signals = [{
            "type": "NEW_TOOL",
            "tool": x.get("ToolName"),
            "vendor": x.get("VendorOrganisation"),
            "evidence": (x.get("EvidenceLinks") or [])[:1],
        } for x in inserted_items[:50]]

        save_json(SIGNALS_JSON, {
            "generated_at_utc": run_ts,
            "signals": signals,
            "broken_links": broken,
        })

        print(f"[OK] discovered={len(discovered)} inserted={inserted} total={len(merged)} broken_links={len(broken)}")
        return 0

    except Exception as e:
        # Write a failure record to signals file (so UI shows it)
        save_json(SIGNALS_JSON, {
            "generated_at_utc": run_ts,
            "signals": [{"type": "ERROR", "message": str(e)}],
            "broken_links": [],
        })
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
