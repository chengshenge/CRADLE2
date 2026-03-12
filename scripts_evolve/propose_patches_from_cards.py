#!/usr/bin/env python3
from __future__ import annotations

"""
LLM-backed proposal generator for patch evolution.

This version truly calls GPT-5-mini (by default) to generate patch proposals.
It keeps the same output contract as the prior heuristic proposer:
- proposal_XX_<patch_name>.json
- proposal_XX_<patch_name>.sidecar.json
- manifest.json

Current controller compatibility:
- Emits patch_type in {prompt, execution} by default.
- Can optionally allow tool/new_tool proposals, but these may not be runnable
  by the current run_round.py unless you extend the controller.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with", "is", "are",
    "was", "were", "be", "by", "that", "this", "it", "as", "at", "from", "all", "how",
    "many", "what", "which", "than", "there", "their", "then", "into", "after", "before",
    "question", "answer", "solution", "objects", "object", "image", "figure", "final", "action",
    "thought", "needed", "display", "print", "python", "correct", "choice", "unit", "please",
}

MEASUREMENT_TERMS = {
    "ml", "g", "kg", "beaker", "cup", "measuring", "capacity", "measures", "measure", "scale",
    "graduated", "fill", "filled", "reading", "glass", "volume", "highest", "marking", "markings",
}
COMPARE_TERMS = {"greater", "fewer", "more", "less", "left", "right", "behind", "front", "yes", "no"}
SET_TERMS = {"subtract", "removed", "remove", "left", "remaining", "count", "counting", "how", "many"}
ATTRIBUTE_TERMS = {
    "metallic", "rubber", "shiny", "matte", "metal", "wooden", "plastic", "gray", "green", "brown",
    "tiny", "small", "large", "big", "red", "blue", "yellow",
}
GENERIC_DANGEROUS_TERMS = {"paraguay", "czechia", "laos", "daphnia", "linda", "wax"}
ALLOWED_PATCH_TYPES_DEFAULT = ["prompt", "execution"]


# ---------- basic IO ----------

def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# ---------- text helpers ----------

def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def preview(text: str, n: int = 500) -> str:
    return normalize_spaces(text)[:n]


def tokenize(text: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9_%°]+", (text or "").lower())


def normalize_answer(ans: Any) -> str:
    s = normalize_spaces(str(ans or "")).lower().strip(" .")
    s = re.sub(r"^[(\[]?([a-e])(?:[)\].:]|\s)+", "", s)
    s = s.replace("—", "-")
    return s


def pred_is_choice_like(pred: Any) -> bool:
    p = normalize_spaces(str(pred or ""))
    return bool(re.fullmatch(r"[(\[]?[A-Ea-e][)\]]?", p)) or p.lower() in {"a", "b", "c", "d", "e"}


def gt_semantically_present_in_response(gt: Any, resp: str) -> bool:
    g = normalize_answer(gt)
    r = normalize_answer(resp)
    if not g or not r:
        return False
    if g in r:
        return True
    g2 = g.replace(" cm", "cm").replace(" ml", "ml")
    r2 = r.replace(" cm", "cm").replace(" ml", "ml")
    if g2 in r2:
        return True
    if g in {"yes", "no"} and re.search(rf"\b{re.escape(g)}\b", r, flags=re.IGNORECASE):
        return True
    return False


# ---------- filtering / mechanism inference ----------

def looks_extractor_resolvable(card: Dict[str, Any], results_index: Optional[Dict[str, Dict[str, Any]]]) -> bool:
    pid = str(card.get("pid"))
    gt = card.get("gt_answer") or ((results_index or {}).get(pid, {}) or {}).get("answer")
    pred = card.get("pred_answer")
    response_preview = str(card.get("response_preview", ""))
    full_response = str((results_index or {}).get(pid, {}).get("response", ""))
    resp = full_response or response_preview
    if pred_is_choice_like(pred) and gt_semantically_present_in_response(gt, resp):
        return True
    if gt and re.search(re.escape(str(gt)), resp, flags=re.IGNORECASE) and re.search(r"[(\[]?[A-Ea-e][)\]]", resp):
        return True
    return False


def should_skip_card(card: Dict[str, Any], results_index: Optional[Dict[str, Dict[str, Any]]]) -> bool:
    if str(card.get("initial_failure_type", "")).strip() == "correct":
        return True
    if looks_extractor_resolvable(card, results_index):
        return True
    return False


def infer_mechanism(text: str) -> str:
    toks = set(tokenize(text))
    if toks & MEASUREMENT_TERMS:
        return "measurement_target"
    if toks & COMPARE_TERMS and (toks & {"greater", "fewer", "more", "less", "left", "right", "behind", "front", "yes", "no"}):
        return "comparison_relation"
    if toks & SET_TERMS and ("subtract" in toks or "remaining" in toks or "left" in toks or "remove" in toks):
        return "set_counting_or_dedup"
    if toks & ATTRIBUTE_TERMS:
        return "attribute_grounding"
    return "generic_reasoning_check"


def cluster_cards(cards: Sequence[Dict[str, Any]], results_index: Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    clusters: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in cards:
        if should_skip_card(c, results_index):
            continue
        pid = str(c.get("pid"))
        full_text = " ".join([
            str(c.get("response_preview", "")),
            str(c.get("gt_answer", "")),
            str(c.get("pred_answer", "")),
            str((results_index or {}).get(pid, {}).get("question", "")),
            str((results_index or {}).get(pid, {}).get("response", ""))[:1400],
        ])
        mech = infer_mechanism(full_text)
        clusters[mech].append(c)
    return dict(clusters)


def cluster_summary(cluster_name: str, rows: Sequence[Dict[str, Any]], results_index: Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
    pids = [str(r.get("pid")) for r in rows]
    token_counter: Counter[str] = Counter()
    previews = []
    for r in rows:
        pid = str(r.get("pid"))
        resp = str(r.get("response_preview", ""))
        q = str((results_index or {}).get(pid, {}).get("question", ""))
        token_counter.update([t for t in tokenize(q + " " + resp) if t not in STOPWORDS])
        previews.append({
            "pid": pid,
            "gt": r.get("gt_answer"),
            "pred": r.get("pred_answer"),
            "question_preview": preview(q, 220),
            "response_preview": preview(resp, 260),
        })
    return {
        "cluster_name": cluster_name,
        "num_cards": len(rows),
        "pids": pids,
        "common_terms": [w for w, _ in token_counter.most_common(12)],
        "examples": previews[:3],
    }


def anti_overfit_findings(common_terms: Sequence[str]) -> Dict[str, Any]:
    dangerous = []
    for t in common_terms:
        if re.search(r"\d", t):
            dangerous.append(t)
        if t in GENERIC_DANGEROUS_TERMS:
            dangerous.append(t)
    return {
        "forbidden_singleton_terms_detected": bool(dangerous),
        "dangerous_terms": sorted(set(dangerous)),
        "overfit_risk": "medium" if dangerous else "low",
    }


# ---------- round report harvesting ----------

def load_round_report(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    if not path.exists():
        raise FileNotFoundError(f"round report not found: {path}")
    return load_json(path)


def round_report_refinement_tasks(report: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    if not report:
        return tasks

    for skipped in report.get("skipped_proposals", []) or []:
        proposal_json = str(skipped.get("proposal_json", ""))
        err = str(skipped.get("error", ""))
        name = Path(proposal_json).stem
        if "stall" in err.lower() or "stalled" in err.lower():
            if "relation_grounded_compare" in name or "comparison" in name:
                tasks.append({
                    "cluster_name": "stall_refinement_comparison_relation",
                    "mode": "round_report_stall_refinement",
                    "patch_hint": "relation_grounded_compare_single_pass_prompt",
                    "num_cards": 1,
                    "examples": [],
                    "apply_to": ["comparison_relation", "attribute_grounding"],
                    "avoid_categories": ["measurement_target", "set_counting_or_dedup"],
                    "report_context": {"stalled_proposal": proposal_json, "error": err},
                })
            elif "measurement" in name:
                tasks.append({
                    "cluster_name": "stall_refinement_measurement_target",
                    "mode": "round_report_stall_refinement",
                    "patch_hint": "measurement_target_single_pass_prompt",
                    "num_cards": 1,
                    "examples": [],
                    "apply_to": ["measurement_target"],
                    "avoid_categories": ["comparison_relation", "set_counting_or_dedup"],
                    "report_context": {"stalled_proposal": proposal_json, "error": err},
                })

    for cand in report.get("candidates", []) or []:
        name = str(cand.get("candidate_name", ""))
        trows = cand.get("target_results", []) or []
        improved = [r for r in trows if r.get("improved_vs_baseline") is True]
        regressed = [r for r in trows if r.get("improved_vs_baseline") is False and r.get("estimated_correct") is False]
        if improved and regressed:
            if "set_object_listing" in name or "object_listing" in name:
                tasks.append({
                    "cluster_name": "refine_set_listing_scope",
                    "mode": "round_report_refinement",
                    "patch_hint": "grounded_evidence_listing_narrowed",
                    "num_cards": len(improved) + len(regressed),
                    "examples": [],
                    "apply_to": ["comparison_relation", "measurement_target", "attribute_grounding"],
                    "avoid_categories": ["set_counting_or_dedup"],
                    "report_context": {
                        "source_candidate": name,
                        "improved_pids": [str(r.get("pid")) for r in improved],
                        "regressed_pids": [str(r.get("pid")) for r in regressed],
                    },
                })
    return tasks


# ---------- OpenAI client ----------

def get_openai_client_or_die() -> Any:
    try:
        from openai import OpenAI  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "Could not import OpenAI SDK. Install/upgrade the official openai package in your environment."
        ) from e
    return OpenAI()


def response_text(resp: Any) -> str:
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text
    # fallback for older SDKs
    try:
        raw = resp.model_dump()
    except Exception:
        raw = None
    if isinstance(raw, dict):
        out = []
        for item in raw.get("output", []) or []:
            for c in item.get("content", []) or []:
                if c.get("type") in {"output_text", "text"} and c.get("text"):
                    out.append(c["text"])
        if out:
            return "\n".join(out)
    return ""


def parse_json_from_text(text: str) -> Dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValueError("empty model output")
    # direct JSON
    try:
        return json.loads(text)
    except Exception:
        pass
    # fenced block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if m:
        return json.loads(m.group(1))
    # first object-like region
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end+1])
    raise ValueError("could not parse JSON from model output")


def build_system_prompt(allowed_patch_types: Sequence[str]) -> str:
    return f"""
You generate generalized patch proposals for a visual reasoning agent benchmark.

Your job is to propose ONE reusable patch per request. The patch must address a failure mechanism cluster,
not memorize a single question. Prefer mechanism-level interventions and avoid single-instance nouns.

Hard rules:
- Return ONLY valid JSON.
- Top-level keys required: patch_name, patch_type, relpath, enable, intended_failure_mechanism,
  should_apply_to, avoid_categories, why_generalizable, risk_of_overfitting.
- Allowed patch_type values: {list(allowed_patch_types)}.
- If patch_type == "prompt", include key: content (string).
- If patch_type == "execution", include key: instructions (array of short strings) and description (string).
- patch_name must be lowercase snake_case and end with _v1 or _v2 etc.
- relpath must start with prompts/ .
- Avoid question-specific nouns, country names, person names, and dataset-specific entities unless they are truly generic.
- If evidence suggests the previous execution proposal stalled, emit a safer bounded single-pass patch rather than another open-ended loop-inducing proposal.

What good proposals look like:
- narrow scope + mechanism clarity
- strong apply_to and avoid_categories
- low or medium overfitting risk
- no direct copying of one question's surface nouns
""".strip()


def build_user_prompt(task: Dict[str, Any], allowed_patch_types: Sequence[str]) -> str:
    schema_hint = {
        "patch_name": "...",
        "patch_type": allowed_patch_types[0] if len(allowed_patch_types) == 1 else "prompt_or_execution",
        "relpath": "prompts/<patch_name>.txt",
        "enable": True,
        "intended_failure_mechanism": task.get("cluster_name"),
        "should_apply_to": task.get("apply_to", []),
        "avoid_categories": task.get("avoid_categories", []),
        "why_generalizable": "...",
        "risk_of_overfitting": "low",
        "content_or_instructions": "depends on patch_type",
    }
    payload = {
        "task": task,
        "allowed_patch_types": list(allowed_patch_types),
        "desired_output_shape_example": schema_hint,
    }
    return (
        "Generate exactly ONE patch proposal for this task.\n"
        "Return only JSON.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def call_llm_for_proposal(client: Any, model: str, task: Dict[str, Any], allowed_patch_types: Sequence[str], reasoning_effort: str) -> Tuple[Dict[str, Any], str]:
    system = build_system_prompt(allowed_patch_types)
    user = build_user_prompt(task, allowed_patch_types)
    kwargs = {
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system}]},
            {"role": "user", "content": [{"type": "input_text", "text": user}]},
        ],
    }
    # GPT-5 family supports reasoning via Responses API.
    if reasoning_effort:
        kwargs["reasoning"] = {"effort": reasoning_effort}

    resp = client.responses.create(**kwargs)
    raw_text = response_text(resp)
    parsed = parse_json_from_text(raw_text)
    return parsed, raw_text


# ---------- validation / materialization ----------

def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", s.strip().lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def normalize_apply_and_avoid(proposal: Dict[str, Any]) -> None:
    proposal["should_apply_to"] = [normalize_spaces(str(x)) for x in proposal.get("should_apply_to", []) if str(x).strip()]
    proposal["avoid_categories"] = [normalize_spaces(str(x)) for x in proposal.get("avoid_categories", []) if str(x).strip()]


def validate_and_split_proposal(proposal: Dict[str, Any], task: Dict[str, Any], raw_text: str, allowed_patch_types: Sequence[str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    p = dict(proposal)
    patch_type = str(p.get("patch_type", "")).strip().lower()
    if patch_type not in set(allowed_patch_types):
        raise ValueError(f"invalid patch_type: {patch_type}")

    patch_name = slugify(str(p.get("patch_name", "")))
    if not patch_name:
        raise ValueError("missing patch_name")
    if not re.search(r"_v\d+$", patch_name):
        patch_name += "_v1"

    relpath = str(p.get("relpath", "")).strip() or f"prompts/{patch_name}.txt"
    if not relpath.startswith("prompts/"):
        relpath = f"prompts/{Path(relpath).name}"

    intended = normalize_spaces(str(p.get("intended_failure_mechanism", ""))) or normalize_spaces(str(task.get("cluster_name", "")))
    why = normalize_spaces(str(p.get("why_generalizable", "")))
    risk = normalize_spaces(str(p.get("risk_of_overfitting", ""))) or "medium"
    enable = bool(p.get("enable", True))

    patch_json: Dict[str, Any] = {
        "patch_name": patch_name,
        "patch_type": patch_type,
        "relpath": relpath,
        "enable": enable,
        "intended_failure_mechanism": intended,
    }
    if patch_type == "prompt":
        content = normalize_spaces(str(p.get("content", "")))
        if not content:
            raise ValueError("prompt proposal missing content")
        patch_json["content"] = content
    elif patch_type == "execution":
        instr = p.get("instructions", [])
        if not isinstance(instr, list) or not instr:
            raise ValueError("execution proposal missing instructions")
        patch_json["instructions"] = [normalize_spaces(str(x)) for x in instr if normalize_spaces(str(x))]
        patch_json["description"] = normalize_spaces(str(p.get("description", "")))

    normalize_apply_and_avoid(p)
    sidecar = {
        "cluster_id": task.get("cluster_name"),
        "proposal": patch_json,
        "intended_failure_mechanism": intended,
        "why_generalizable": why,
        "risk_of_overfitting": risk,
        "forbidden_singleton_terms_detected": bool(p.get("forbidden_singleton_terms_detected", False)),
        "dangerous_terms": list(p.get("dangerous_terms", [])),
        "cluster_summary": task.get("cluster_summary", task),
        "apply_to": p.get("should_apply_to", task.get("apply_to", [])),
        "avoid_categories": p.get("avoid_categories", task.get("avoid_categories", [])),
        "mode": task.get("mode", "cluster"),
        "llm_model": task.get("_llm_model"),
        "raw_model_text_preview": preview(raw_text, 1800),
    }
    return patch_json, sidecar


# ---------- task assembly ----------

def build_tasks(
    discovery_cards: Sequence[Dict[str, Any]],
    results_index: Dict[str, Dict[str, Any]],
    report: Optional[Dict[str, Any]],
    include_generic: bool,
) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []

    # 1) round-report-driven refinements first
    tasks.extend(round_report_refinement_tasks(report))

    # 2) baseline discovery clusters
    clusters = cluster_cards(discovery_cards, results_index)
    for cluster_name, rows in sorted(clusters.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if cluster_name == "generic_reasoning_check" and not include_generic:
            continue
        summary = cluster_summary(cluster_name, rows, results_index)
        anti = anti_overfit_findings(summary.get("common_terms", []))
        apply_to = [cluster_name]
        avoid = []
        if cluster_name == "comparison_relation":
            apply_to = ["comparison_relation", "attribute_grounding"]
            avoid = ["measurement_target"]
        elif cluster_name == "measurement_target":
            apply_to = ["measurement_target"]
        elif cluster_name == "set_counting_or_dedup":
            apply_to = ["set_counting_or_dedup"]
        elif cluster_name == "attribute_grounding":
            apply_to = ["attribute_grounding", "comparison_relation"]
            avoid = ["measurement_target"]
        tasks.append({
            "cluster_name": cluster_name,
            "mode": "cluster",
            "num_cards": len(rows),
            "apply_to": apply_to,
            "avoid_categories": avoid,
            "cluster_summary": summary,
            "anti_overfit": anti,
        })
    return tasks


# ---------- main ----------

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discovery-cards", required=True)
    ap.add_argument("--results-file", required=True)
    ap.add_argument("--round-report", default="")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--max-proposals", type=int, default=4)
    ap.add_argument("--prefer-refinements", action="store_true")
    ap.add_argument("--include-generic", action="store_true")
    ap.add_argument("--meta-model", default="gpt-5-mini")
    ap.add_argument("--reasoning-effort", default="medium", choices=["minimal", "low", "medium", "high"])
    ap.add_argument("--allowed-patch-types", nargs="*", default=ALLOWED_PATCH_TYPES_DEFAULT)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    discovery_path = Path(args.discovery_cards)
    results_path = Path(args.results_file)
    out_dir = Path(args.out_dir)
    report_path = Path(args.round_report) if args.round_report else None

    discovery_cards = load_jsonl(discovery_path)
    results_obj = load_json(results_path)
    results_index = {str(k): v for k, v in (results_obj or {}).items()}
    report = load_round_report(report_path)

    tasks = build_tasks(discovery_cards, results_index, report, include_generic=args.include_generic)
    if args.prefer_refinements:
        tasks.sort(key=lambda t: (0 if str(t.get("mode", "")).startswith("round_report") else 1, -int(t.get("num_cards", 0))))
    else:
        tasks.sort(key=lambda t: (-int(t.get("num_cards", 0)), t.get("cluster_name", "")))

    if not tasks:
        raise SystemExit("No eligible tasks found for proposal generation.")

    client = get_openai_client_or_die()

    emitted: List[Dict[str, Any]] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    for idx, task in enumerate(tasks[: args.max_proposals], start=1):
        task = dict(task)
        task["_llm_model"] = args.meta_model
        try:
            proposal_obj, raw_text = call_llm_for_proposal(
                client=client,
                model=args.meta_model,
                task=task,
                allowed_patch_types=args.allowed_patch_types,
                reasoning_effort=args.reasoning_effort,
            )
            patch_json, sidecar = validate_and_split_proposal(
                proposal_obj, task=task, raw_text=raw_text, allowed_patch_types=args.allowed_patch_types
            )
        except Exception as e:
            # emit an error sidecar for debugging and continue
            err_name = out_dir / f"proposal_{idx:02d}_ERROR.sidecar.json"
            write_json(err_name, {
                "cluster_id": task.get("cluster_name"),
                "mode": task.get("mode"),
                "error": str(e),
            })
            continue

        patch_name = patch_json["patch_name"]
        proposal_path = out_dir / f"proposal_{idx:02d}_{patch_name}.json"
        sidecar_path = out_dir / f"proposal_{idx:02d}_{patch_name}.sidecar.json"
        write_json(proposal_path, patch_json)
        write_json(sidecar_path, sidecar)
        emitted.append({
            "proposal_json": str(proposal_path.resolve()),
            "sidecar_json": str(sidecar_path.resolve()),
            "cluster": task.get("cluster_name"),
            "num_cards": int(task.get("num_cards", 0)),
            "patch_name": patch_name,
            "risk_of_overfitting": sidecar.get("risk_of_overfitting", "medium"),
            "mode": task.get("mode", "cluster"),
            "apply_to": sidecar.get("apply_to", []),
            "avoid_categories": sidecar.get("avoid_categories", []),
            "patch_type": patch_json.get("patch_type"),
            "llm_model": args.meta_model,
        })

    manifest = {
        "llm_backed": True,
        "meta_model": args.meta_model,
        "reasoning_effort": args.reasoning_effort,
        "allowed_patch_types": list(args.allowed_patch_types),
        "num_input_cards": len(discovery_cards),
        "num_tasks": len(tasks),
        "num_emitted_proposals": len(emitted),
        "proposals": emitted,
    }
    write_json(out_dir / "manifest.json", manifest)

    print(f"Wrote {len(emitted)} proposal(s) to {out_dir}")
    for row in emitted:
        print(
            f"- {row['patch_name']} type={row['patch_type']} cluster={row['cluster']} cards={row['num_cards']} "
            f"mode={row['mode']} apply_to={row['apply_to']} avoid={row['avoid_categories']} llm={row['llm_model']}"
        )


if __name__ == "__main__":
    main()
