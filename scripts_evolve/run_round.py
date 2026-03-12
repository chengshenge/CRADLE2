#!/usr/bin/env python3
"""
Round controller v5.

Upgrades over v4:
- Stability-aware execution materialization: execution proposals are rendered into bounded
  prompt workflows with "single-pass" / "no recursive recount" rules to reduce hangs.
- Richer hang snapshots: when a candidate stalls, the controller records the latest qdir,
  recent files, request preview, and output/exception presence so skipped proposals are easier
  to debug and refine.
- Candidate hang guard from v4 remains in place.
- Robust meta proposal discovery still ignores .sidecar.json files via manifest or glob.
- Keeps richer target scoring fields from v4.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def shlex_quote(s: str) -> str:
    import shlex
    return shlex.quote(str(s))


def run_cmd(cmd: Sequence[str], cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> None:
    pretty = " ".join(shlex_quote(x) for x in cmd)
    print(f"\n$ {pretty}")
    subprocess.run(list(cmd), cwd=str(cwd) if cwd else None, env=env, check=True)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def ensure_exists(path: Path, what: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{what} not found: {path}")


def normalize_text(text: Any) -> str:
    s = str(text or "")
    s = s.replace("\u00b0", "°")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_answer(ans: Any) -> str:
    s = normalize_text(ans).lower()
    s = s.replace("answer:", "").strip(" .")
    s = re.sub(r"^[(\[]?([a-e])(?:[)\].:]|\s)+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def answers_equivalent(gt: Any, pred: Any) -> bool:
    g = normalize_answer(gt)
    p = normalize_answer(pred)
    if not g or not p:
        return False
    if g == p:
        return True
    if g in p or p in g:
        return True
    g2 = g.replace(" cm", "cm").replace(" ml", "ml")
    p2 = p.replace(" cm", "cm").replace(" ml", "ml")
    if g2 == p2:
        return True
    if g in {"yes", "no"} and p.startswith(g):
        return True
    return False


def extract_last_answer_span(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.findall(r"ANSWER\s*:\s*(.+)", text, flags=re.IGNORECASE)
    if m:
        return normalize_text(m[-1])
    return None


def extract_numeric_or_unit(text: str) -> Optional[str]:
    if not text:
        return None
    patterns = [
        r"(-?\d+(?:\.\d+)?)\s*(cm|mm|m|ml|l|kg|g|%)\b",
        r"\b(-?\d+(?:\.\d+)?)\b",
    ]
    for pat in patterns:
        ms = re.findall(pat, text, flags=re.IGNORECASE)
        if ms:
            last = ms[-1]
            if isinstance(last, tuple):
                return normalize_text("".join(last))
            return normalize_text(last)
    return None


def extract_yes_no(text: str) -> Optional[str]:
    if not text:
        return None
    lowered = normalize_text(text).lower()
    if re.search(r"\banswer\s*:\s*yes\b", lowered):
        return "Yes"
    if re.search(r"\banswer\s*:\s*no\b", lowered):
        return "No"
    if re.search(r"\byes\b", lowered):
        return "Yes"
    if re.search(r"\bno\b", lowered):
        return "No"
    return None


def extract_content_before_choice(answer_line: str) -> Optional[str]:
    if not answer_line:
        return None
    s = normalize_text(answer_line)
    s = re.sub(r"^[(\[]?[A-Ea-e][)\]]\s*", "", s)
    s = re.sub(r"\s*[(\[]?[A-Ea-e][)\]]\s*$", "", s)
    s = s.strip(" .")
    return s or None


def extract_pred_answer_from_record(record: Dict[str, Any]) -> Any:
    if not record:
        return None
    for k in ["pred_answer", "prediction", "response_extracted_answer", "response_answer"]:
        v = record.get(k)
        if v not in (None, ""):
            return v

    response = str(record.get("response", ""))
    answer_line = extract_last_answer_span(response)
    if answer_line:
        content = extract_content_before_choice(answer_line)
        yn = extract_yes_no(answer_line)
        num = extract_numeric_or_unit(answer_line)
        if content and normalize_answer(content) not in {"a", "b", "c", "d", "e"}:
            return content
        if yn:
            return yn
        if num:
            return num
        return answer_line

    yn = extract_yes_no(response)
    if yn:
        return yn
    num = extract_numeric_or_unit(response)
    if num:
        return num
    return None


def estimated_correct(record: Dict[str, Any]) -> Optional[bool]:
    if not record:
        return None
    gt = record.get("answer")
    if gt in (None, ""):
        gt = record.get("gt_answer")
    pred = extract_pred_answer_from_record(record)
    if gt in (None, "") or pred in (None, ""):
        return None
    return answers_equivalent(gt, pred)


@dataclass
class TargetResult:
    pid: str
    gt_answer: Any
    pred_answer: Any
    baseline_pred_answer: Any
    estimated_correct: Optional[bool]
    baseline_estimated_correct: Optional[bool]
    improved_vs_baseline: Optional[bool]
    response_preview: str


@dataclass
class CandidateSummary:
    candidate_name: str
    original_patch_type: str
    applied_patch_type: str
    proposal_json: str
    materialized_proposal_json: str
    patch_config: str
    output_dir: str
    results_file: str
    val_cards_file: str
    num_val_cards: int
    val_correct: int
    val_incorrect: int
    val_failure_types: Dict[str, int]
    target_results: List[TargetResult]
    recommendation: str
    rationale: str


def load_results_index(path: Path) -> Dict[str, Dict[str, Any]]:
    obj = load_json(path)
    if not isinstance(obj, dict):
        raise ValueError(f"results file is not a dict: {path}")
    return {str(k): v for k, v in obj.items()}


def parse_target_pids(s: str) -> List[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def load_split_ids(split_json: Path, split_key: str) -> List[str]:
    obj = load_json(split_json)
    if split_key not in obj:
        return []
    vals = obj[split_key]
    if isinstance(vals, dict):
        vals = list(vals.keys())
    return [str(x) for x in vals]


def summarize_val(cards: Sequence[Dict[str, Any]]) -> Tuple[int, int, int, Dict[str, int]]:
    num = len(cards)
    correct = sum(1 for c in cards if c.get("is_correct") is True)
    incorrect = num - correct
    failure_types = Counter(
        ("correct" if c.get("is_correct") is True else str(c.get("initial_failure_type", "unknown")))
        for c in cards
    )
    return num, correct, incorrect, dict(failure_types)


def build_target_results(current: Dict[str, Dict[str, Any]], baseline: Optional[Dict[str, Dict[str, Any]]], pids: Sequence[str]) -> List[TargetResult]:
    out: List[TargetResult] = []
    for pid in pids:
        rec = current.get(str(pid), {})
        base = baseline.get(str(pid), {}) if baseline else {}
        gt = rec.get("answer") if rec.get("answer") not in (None, "") else base.get("answer")
        pred = extract_pred_answer_from_record(rec)
        base_pred = extract_pred_answer_from_record(base) if base else None
        cur_ok = estimated_correct(rec)
        base_ok = estimated_correct(base) if base else None
        if cur_ok is None:
            improved = None
        elif base_ok is None:
            improved = True if cur_ok is True else False
        else:
            improved = (cur_ok is True and base_ok is not True)
        out.append(TargetResult(
            pid=str(pid),
            gt_answer=gt,
            pred_answer=pred,
            baseline_pred_answer=base_pred,
            estimated_correct=cur_ok,
            baseline_estimated_correct=base_ok,
            improved_vs_baseline=improved,
            response_preview=normalize_text(str(rec.get("response", "")))[:1500],
        ))
    return out


def make_recommendation(target_results: Sequence[TargetResult], val_correct: int, baseline_val_correct: Optional[int]) -> Tuple[str, str]:
    improved_targets = [t for t in target_results if t.improved_vs_baseline is True]
    cur_target_correct = sum(1 for t in target_results if t.estimated_correct is True)
    base_target_correct = sum(1 for t in target_results if t.baseline_estimated_correct is True)

    if improved_targets and (baseline_val_correct is None or val_correct >= baseline_val_correct):
        return "accept_candidate", f"Improved target discovery PIDs: {[t.pid for t in improved_targets]} with non-worse val performance."
    if improved_targets:
        return "hold", f"Improved target discovery PIDs: {[t.pid for t in improved_targets]} but val dropped versus baseline."
    if cur_target_correct > base_target_correct:
        return "hold", "Some target discovery correctness improved, but improvement could not be cleanly attributed per-PID."
    return "reject", "No target discovery PID showed clear improvement versus baseline."


def detect_patch_name_from_proposal(proposal_json: Path) -> str:
    obj = load_json(proposal_json)
    name = obj.get("patch_name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"proposal {proposal_json} missing valid patch_name")
    return name.strip()


def make_patch_config_path(config_dir: Path, patch_name: str) -> Path:
    return config_dir / f"active_patches.candidate_{patch_name}.json"


def candidate_output_dir(results_dir: Path, patch_name: str, run_suffix: str) -> Path:
    return results_dir / f"round0_candidate_{patch_name}_{run_suffix}"


def candidate_val_cards_path(analysis_dir: Path, patch_name: str, run_suffix: str) -> Path:
    return analysis_dir / f"val_cards_{patch_name}_{run_suffix}.jsonl"


def execution_gating_for_mechanism(mechanism: str) -> Tuple[str, List[str], List[str]]:
    mech = (mechanism or "").strip().lower()
    if mech == "comparison_relation":
        gate = (
            "Apply the workflow below only if the question explicitly asks for a comparison between two sets or quantities, "
            "for example using words such as greater, fewer, more, less, left of, right of, behind, in front of, yes, or no. "
            "If the question is not a comparison question, ignore this workflow completely and answer normally."
        )
        positives = ["greater", "fewer", "more", "less", "left of", "right of", "behind", "in front of", "yes/no"]
        negatives = ["total volume", "capacity", "measure", "reading", "what is the total", "highest amount"]
        return gate, positives, negatives
    if mech == "measurement_target":
        gate = (
            "Apply the workflow below only if the question is asking about a measured quantity or labeled scale value, "
            "for example current reading, current fill level, highest labeled mark, printed capacity, or nominal label value. "
            "If the question is not a measurement-reading question, ignore this workflow completely and answer normally."
        )
        positives = ["measure", "measuring cup", "beaker", "scale", "capacity", "fill level", "reading", "highest amount"]
        negatives = ["greater", "fewer", "more", "less", "left of", "right of", "behind", "in front of"]
        return gate, positives, negatives
    if mech == "set_counting_or_dedup":
        gate = (
            "Apply the workflow below only if the question asks you to subtract, remove, or count remaining objects after filtering objects by one or more conditions. "
            "If the question is not an object-removal or remaining-count question, ignore this workflow completely and answer normally."
        )
        positives = ["subtract", "remove", "remaining", "objects are left", "how many objects are left"]
        negatives = ["total volume", "capacity", "greater", "fewer", "more", "less"]
        return gate, positives, negatives
    if mech == "attribute_grounding":
        gate = (
            "Apply the workflow below only if the question depends on counting or comparing objects that satisfy specific visual attributes. "
            "If the question does not require attribute-grounded object identification, ignore this workflow completely and answer normally."
        )
        positives = ["metallic", "rubber", "shiny", "matte", "gray", "green", "brown", "tiny"]
        negatives = ["capacity", "fill level", "highest amount"]
        return gate, positives, negatives
    gate = (
        "Apply the workflow below only if the question clearly matches the described failure mechanism. "
        "Otherwise ignore this workflow completely and answer normally."
    )
    return gate, [], []


def render_execution_patch_to_prompt(obj: Dict[str, Any]) -> Dict[str, Any]:
    patch_name = str(obj["patch_name"])
    relpath = str(obj.get("relpath") or f"prompts/{patch_name}.txt")
    steps = obj.get("instructions") or obj.get("steps") or []
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"execution proposal {patch_name} missing non-empty instructions list")
    mechanism = str(obj.get("intended_failure_mechanism") or obj.get("target") or "").strip().lower()
    header = normalize_text(obj.get("description") or "Follow this execution workflow before giving the final answer:")
    gate_text, positives, negatives = execution_gating_for_mechanism(mechanism)

    lines = [gate_text, "", header]
    if positives:
        lines.append("Use this workflow when the question matches cues such as: " + ", ".join(positives) + ".")
    if negatives:
        lines.append("Do not use this workflow when the question instead looks like: " + ", ".join(negatives) + ".")
    lines.append("")
    stability_lines: List[str] = []
    if mechanism == "comparison_relation":
        stability_lines = [
            "Use at most one full-image inspection and at most one additional zoom/crop per compared set.",
            "Do not repeatedly re-count or recursively spawn more visual checks once the two grounded sets have been listed once.",
            "If evidence remains ambiguous after the single pass, stop and answer from the best grounded counts already obtained.",
        ]
    elif mechanism == "measurement_target":
        stability_lines = [
            "Use at most one focused inspection of the relevant scale/label region.",
            "Do not alternate repeatedly between reading labels and re-zooming the same region.",
            "After mapping the requested quantity type to one visible candidate, stop and answer.",
        ]
    elif mechanism == "set_counting_or_dedup":
        stability_lines = [
            "Build the scene inventory only once.",
            "Do not repeatedly rebuild the full object list or re-subtract the same conditions.",
            "After the removed and remaining sets are written once, stop and answer.",
        ]
    elif mechanism == "attribute_grounding":
        stability_lines = [
            "List each queried attribute class at most once.",
            "Do not repeatedly revisit the same attribute-based count after the grounded list has been written once.",
            "If some candidates remain uncertain after the single pass, stop and answer from the grounded list you already have.",
        ]
    if stability_lines:
        lines.append("Stability constraints:")
        for s in stability_lines:
            lines.append(f"- {normalize_text(s)}")
        lines.append("")
    for i, step in enumerate(steps, 1):
        lines.append(f"{i}. {normalize_text(step)}")
    lines.append("Only after these steps are completed should you produce the final answer.")
    return {
        "patch_name": patch_name,
        "patch_type": "prompt",
        "relpath": relpath,
        "content": "\n".join(lines),
        "enable": bool(obj.get("enable", True)),
    }


def materialize_compatible_proposal(proposal_json: Path, materialized_dir: Path) -> Tuple[Path, str, str]:
    obj = load_json(proposal_json)
    patch_type = str(obj.get("patch_type", "prompt")).strip() or "prompt"
    patch_name = detect_patch_name_from_proposal(proposal_json)
    if patch_type == "prompt":
        return proposal_json, patch_type, patch_type
    if patch_type == "execution":
        materialized = render_execution_patch_to_prompt(obj)
        out_path = materialized_dir / f"materialized_{patch_name}.json"
        write_json(out_path, materialized)
        return out_path, patch_type, "prompt"
    if patch_type in {"tool", "new_tool"}:
        raise ValueError(f"Unsupported proposal patch_type for current repo: {patch_type}. Use prompt/execution only.")
    raise ValueError(f"Unknown patch_type {patch_type} in {proposal_json}")


def maybe_generate_meta_proposals(
    repo_root: Path,
    split_json: Path,
    discovery_cards: Path,
    discovery_results: Optional[Path],
    out_dir: Path,
    model: str,
    max_proposals: int,
    include_generic: bool,
) -> List[Path]:
    cmd = [
        sys.executable,
        "scripts_evolve/propose_patches_from_cards.py",
        "--discovery-cards", str(discovery_cards),
        "--out-dir", str(out_dir),
        "--model", model,
        "--max-proposals", str(max_proposals),
    ]
    if discovery_results is not None:
        cmd.extend(["--results-file", str(discovery_results)])
    if split_json.exists():
        cmd.extend(["--split-json", str(split_json), "--split-key", "discovery"])
    if include_generic:
        cmd.append("--include-generic")
    run_cmd(cmd, cwd=repo_root)
    manifest = out_dir / "manifest.json"
    if manifest.exists():
        man = load_json(manifest)
        props = []
        for row in man.get("proposals", []):
            p = Path(row["proposal_json"])
            if p.name.endswith(".sidecar.json"):
                continue
            props.append(p)
        return props
    return sorted(
        p for p in out_dir.glob("proposal_*.json")
        if not p.name.endswith(".sidecar.json")
    )


def latest_trace_activity(trace_root: Path) -> Optional[float]:
    if not trace_root.exists():
        return None
    latest: Optional[float] = None
    for p in trace_root.rglob("*"):
        try:
            if p.is_file():
                m = p.stat().st_mtime
                if latest is None or m > latest:
                    latest = m
        except FileNotFoundError:
            continue
    return latest


def latest_trace_dir(trace_root: Path) -> Optional[Path]:
    if not trace_root.exists():
        return None
    dirs = [p for p in trace_root.iterdir() if p.is_dir()]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def safe_load_json(path: Path) -> Any:
    try:
        return load_json(path)
    except Exception:
        return None


def recent_trace_files(trace_dir: Optional[Path], limit: int = 20) -> List[str]:
    if trace_dir is None or not trace_dir.exists():
        return []
    rows: List[Tuple[float, str]] = []
    for p in trace_dir.rglob("*"):
        try:
            if p.is_file():
                rows.append((p.stat().st_mtime, str(p)))
        except Exception:
            continue
    rows.sort()
    return [r[1] for r in rows[-limit:]]


def make_guard_snapshot(trace_root: Path, hard_timeout_seconds: int, stall_timeout_seconds: int, start: float, last_activity: float, last_seen_trace_ts: Optional[float], reason: str) -> Dict[str, Any]:
    latest_dir = latest_trace_dir(trace_root)
    snap: Dict[str, Any] = {
        "reason": reason,
        "hard_timeout_seconds": hard_timeout_seconds,
        "stall_timeout_seconds": stall_timeout_seconds,
        "elapsed_seconds": time.time() - start,
        "seconds_since_last_trace_activity": None if last_seen_trace_ts is None else (time.time() - last_activity),
        "latest_trace_dir": str(latest_dir or ""),
        "latest_trace_mtime": last_seen_trace_ts,
        "recent_files": recent_trace_files(latest_dir, limit=30),
    }
    if latest_dir is not None:
        req = latest_dir / "request.json"
        out = latest_dir / "output.json"
        aexc = latest_dir / "assistant_receive_exception.json"
        iexc = latest_dir / "agent_initiate_chat_exception.json"
        req_obj = safe_load_json(req)
        if isinstance(req_obj, dict):
            snap["latest_question_preview"] = normalize_text(str(req_obj.get("question") or req_obj.get("query") or ""))[:300]
        snap["has_output_json"] = out.exists()
        snap["has_assistant_exception"] = aexc.exists()
        snap["has_initiate_exception"] = iexc.exists()
    return snap


def write_guard_snapshot(output_dir: Path, payload: Dict[str, Any]) -> None:
    try:
        write_json(output_dir / "controller_guard_snapshot.json", payload)
    except Exception:
        pass


def terminate_process_tree(proc: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=15)
        return
    except Exception:
        pass
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run_generate_with_guard(
    cmd: Sequence[str],
    cwd: Path,
    output_dir: Path,
    hard_timeout_seconds: int,
    stall_timeout_seconds: int,
    poll_seconds: int = 5,
) -> None:
    pretty = " ".join(shlex_quote(x) for x in cmd)
    print(f"\n$ {pretty}")
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_root = output_dir / "vsketchpad_traces"
    start = time.time()
    last_activity = start
    last_seen_trace_ts: Optional[float] = None

    proc = subprocess.Popen(
        list(cmd),
        cwd=str(cwd),
        preexec_fn=os.setsid,
    )
    try:
        while True:
            ret = proc.poll()
            now = time.time()
            latest = latest_trace_activity(trace_root)
            if latest is not None:
                if last_seen_trace_ts is None or latest > last_seen_trace_ts:
                    last_seen_trace_ts = latest
                    last_activity = now
            if ret is not None:
                if ret != 0:
                    raise subprocess.CalledProcessError(ret, list(cmd))
                return
            if hard_timeout_seconds > 0 and now - start > hard_timeout_seconds:
                snap = make_guard_snapshot(
                    trace_root=trace_root,
                    hard_timeout_seconds=hard_timeout_seconds,
                    stall_timeout_seconds=stall_timeout_seconds,
                    start=start,
                    last_activity=last_activity,
                    last_seen_trace_ts=last_seen_trace_ts,
                    reason="hard_timeout",
                )
                write_guard_snapshot(output_dir, snap)
                terminate_process_tree(proc)
                raise TimeoutError(f"generate_response exceeded hard timeout of {hard_timeout_seconds}s for {output_dir}")
            if stall_timeout_seconds > 0 and last_seen_trace_ts is not None and now - last_activity > stall_timeout_seconds:
                snap = make_guard_snapshot(
                    trace_root=trace_root,
                    hard_timeout_seconds=hard_timeout_seconds,
                    stall_timeout_seconds=stall_timeout_seconds,
                    start=start,
                    last_activity=last_activity,
                    last_seen_trace_ts=last_seen_trace_ts,
                    reason="trace_stall",
                )
                write_guard_snapshot(output_dir, snap)
                terminate_process_tree(proc)
                raise TimeoutError(f"generate_response trace activity stalled for > {stall_timeout_seconds}s in {output_dir}")
            time.sleep(max(1, poll_seconds))
    finally:
        if proc.poll() is None:
            terminate_process_tree(proc)


def process_candidate(
    repo_root: Path,
    proposal_json: Path,
    patch_name: str,
    patch_root: Path,
    base_patch_config: Path,
    generated_patch_config: Path,
    run_suffix: str,
    results_dir: Path,
    analysis_dir: Path,
    split_json: Path,
    val_split_key: str,
    baseline_results: Optional[Dict[str, Dict[str, Any]]],
    baseline_val_cards: Optional[List[Dict[str, Any]]],
    target_pids: Sequence[str],
    model: str,
    max_num_problems: int,
    keep_traces: bool,
    patch_debug_dump: bool,
    rerun: bool,
    extra_generate_args: Sequence[str],
    generate_hard_timeout_seconds: int,
    generate_stall_timeout_seconds: int,
) -> CandidateSummary:
    materialized_dir = analysis_dir / "_materialized_proposals"
    materialized_proposal, original_ptype, applied_ptype = materialize_compatible_proposal(proposal_json, materialized_dir)

    run_cmd(
        [
            sys.executable,
            "scripts_evolve/apply_patch_proposal.py",
            "--proposal-json", str(materialized_proposal),
            "--patch-root", str(patch_root),
            "--base-config", str(base_patch_config),
            "--out-config", str(generated_patch_config),
        ],
        cwd=repo_root,
    )

    output_dir = candidate_output_dir(results_dir, patch_name, run_suffix)
    results_file = output_dir / "out.json"
    cmd = [
        sys.executable,
        "-m", "evaluation.generate_response",
        "--agent", "visual_sketchpad",
        "--model", model,
        "--test_split_name", "testmini",
        "--max_num_problems", str(max_num_problems),
        "--output_dir", str(output_dir),
        "--output_file", "out.json",
        "--patch_config", str(generated_patch_config),
        "--patch_root", str(patch_root),
    ]
    if keep_traces:
        cmd.append("--vsk_keep_traces")
    if patch_debug_dump:
        cmd.append("--patch_debug_dump")
    if rerun:
        cmd.append("--rerun")
    cmd.extend(extra_generate_args)
    run_generate_with_guard(
        cmd,
        cwd=repo_root,
        output_dir=output_dir,
        hard_timeout_seconds=generate_hard_timeout_seconds,
        stall_timeout_seconds=generate_stall_timeout_seconds,
    )
    ensure_exists(results_file, "candidate results")

    val_cards_file = candidate_val_cards_path(analysis_dir, patch_name, run_suffix)
    run_cmd(
        [
            sys.executable,
            "scripts_evolve/make_diagnostic_cards.py",
            "--results-file", str(results_file),
            "--traces-root", str(output_dir / "vsketchpad_traces"),
            "--split-json", str(split_json),
            "--split-key", val_split_key,
            "--out-jsonl", str(val_cards_file),
        ],
        cwd=repo_root,
    )
    ensure_exists(val_cards_file, "val cards")

    current_results = load_results_index(results_file)
    val_cards = load_jsonl(val_cards_file)
    num_val, val_correct, val_incorrect, val_failure_types = summarize_val(val_cards)
    baseline_val_correct = None if baseline_val_cards is None else sum(1 for c in baseline_val_cards if c.get("is_correct") is True)
    target_results = build_target_results(current_results, baseline_results, target_pids)
    recommendation, rationale = make_recommendation(target_results, val_correct, baseline_val_correct)

    return CandidateSummary(
        candidate_name=patch_name,
        original_patch_type=original_ptype,
        applied_patch_type=applied_ptype,
        proposal_json=str(proposal_json),
        materialized_proposal_json=str(materialized_proposal),
        patch_config=str(generated_patch_config),
        output_dir=str(output_dir),
        results_file=str(results_file),
        val_cards_file=str(val_cards_file),
        num_val_cards=num_val,
        val_correct=val_correct,
        val_incorrect=val_incorrect,
        val_failure_types=val_failure_types,
        target_results=target_results,
        recommendation=recommendation,
        rationale=rationale,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run patch candidates and emit a round summary.")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--proposal-jsons", nargs="*", default=[])
    p.add_argument("--meta-discovery-cards", default="")
    p.add_argument("--meta-results-file", default="")
    p.add_argument("--meta-out-dir", default="analysis/meta_proposals")
    p.add_argument("--meta-max-proposals", type=int, default=3)
    p.add_argument("--meta-include-generic", action="store_true")
    p.add_argument("--patch-root", default="generated_patches")
    p.add_argument("--base-patch-config", default="configs/active_patches.json")
    p.add_argument("--split-json", default="splits/mathvista_testmini_round0.json")
    p.add_argument("--val-split-key", default="val")
    p.add_argument("--analysis-dir", default="analysis")
    p.add_argument("--results-dir", default="results")
    p.add_argument("--run-suffix", default="autoround")
    p.add_argument("--model", default="gpt-5-mini")
    p.add_argument("--max-num-problems", type=int, default=20)
    p.add_argument("--target-pids", required=True)
    p.add_argument("--baseline-results", default="")
    p.add_argument("--baseline-val-cards", default="")
    p.add_argument("--report-out", default="analysis/round_report_autoround.json")
    p.add_argument("--keep-traces", action="store_true", default=True)
    p.add_argument("--no-keep-traces", dest="keep_traces", action="store_false")
    p.add_argument("--patch-debug-dump", action="store_true", default=True)
    p.add_argument("--no-patch-debug-dump", dest="patch_debug_dump", action="store_false")
    p.add_argument("--rerun", action="store_true", default=True)
    p.add_argument("--no-rerun", dest="rerun", action="store_false")
    p.add_argument("--generate-hard-timeout-seconds", type=int, default=7200)
    p.add_argument("--generate-stall-timeout-seconds", type=int, default=420)
    p.add_argument("--extra-generate-args", nargs=argparse.REMAINDER, default=[])
    return p


def main() -> None:
    args = build_parser().parse_args()
    repo_root = Path(args.repo_root).resolve()
    patch_root = (repo_root / args.patch_root).resolve()
    base_patch_config = (repo_root / args.base_patch_config).resolve()
    split_json = (repo_root / args.split_json).resolve()
    analysis_dir = (repo_root / args.analysis_dir).resolve()
    results_dir = (repo_root / args.results_dir).resolve()
    report_out = (repo_root / args.report_out).resolve()

    ensure_exists(repo_root, "repo root")
    ensure_exists(base_patch_config, "base patch config")
    ensure_exists(split_json, "split json")

    baseline_results = None
    if args.baseline_results:
        baseline_results_path = (repo_root / args.baseline_results).resolve()
        ensure_exists(baseline_results_path, "baseline results")
        baseline_results = load_results_index(baseline_results_path)

    baseline_val_cards = None
    if args.baseline_val_cards:
        baseline_val_cards_path = (repo_root / args.baseline_val_cards).resolve()
        ensure_exists(baseline_val_cards_path, "baseline val cards")
        baseline_val_cards = load_jsonl(baseline_val_cards_path)

    target_pids = parse_target_pids(args.target_pids)
    if not target_pids:
        raise ValueError("--target-pids cannot be empty")

    val_ids = load_split_ids(split_json, args.val_split_key)
    discovery_cards_path = (repo_root / args.meta_discovery_cards).resolve() if args.meta_discovery_cards else None
    discovery_results_path = (repo_root / args.meta_results_file).resolve() if args.meta_results_file else None

    proposal_paths: List[Path] = []
    if args.proposal_jsons:
        for proposal in args.proposal_jsons:
            p = (repo_root / proposal).resolve()
            ensure_exists(p, "proposal json")
            if p.name.endswith(".sidecar.json"):
                continue
            proposal_paths.append(p)
    if discovery_cards_path is not None:
        ensure_exists(discovery_cards_path, "meta discovery cards")
        meta_out_dir = (repo_root / args.meta_out_dir).resolve()
        proposal_paths.extend(
            maybe_generate_meta_proposals(
                repo_root=repo_root,
                split_json=split_json,
                discovery_cards=discovery_cards_path,
                discovery_results=discovery_results_path,
                out_dir=meta_out_dir,
                model=args.model,
                max_proposals=args.meta_max_proposals,
                include_generic=args.meta_include_generic,
            )
        )

    seen = set()
    uniq: List[Path] = []
    for p in proposal_paths:
        s = str(p)
        if s not in seen:
            uniq.append(p)
            seen.add(s)
    proposal_paths = uniq

    if not proposal_paths:
        raise ValueError("No proposal JSONs supplied or generated.")

    print("== Round controller v5 ==")
    print(f"repo_root: {repo_root}")
    print(f"model: {args.model}")
    print(f"max_num_problems: {args.max_num_problems}")
    print(f"target_pids: {target_pids}")
    print(f"val_ids: {val_ids}")
    print(f"num_candidates: {len(proposal_paths)}")
    print(f"generate_hard_timeout_seconds: {args.generate_hard_timeout_seconds}")
    print(f"generate_stall_timeout_seconds: {args.generate_stall_timeout_seconds}")

    summaries: List[CandidateSummary] = []
    skipped: List[Dict[str, Any]] = []
    for proposal_path in proposal_paths:
        try:
            patch_name = detect_patch_name_from_proposal(proposal_path)
            generated_patch_config = (repo_root / make_patch_config_path(Path("configs"), patch_name)).resolve()
            summaries.append(
                process_candidate(
                    repo_root=repo_root,
                    proposal_json=proposal_path,
                    patch_name=patch_name,
                    patch_root=patch_root,
                    base_patch_config=base_patch_config,
                    generated_patch_config=generated_patch_config,
                    run_suffix=args.run_suffix,
                    results_dir=results_dir,
                    analysis_dir=analysis_dir,
                    split_json=split_json,
                    val_split_key=args.val_split_key,
                    baseline_results=baseline_results,
                    baseline_val_cards=baseline_val_cards,
                    target_pids=target_pids,
                    model=args.model,
                    max_num_problems=args.max_num_problems,
                    keep_traces=args.keep_traces,
                    patch_debug_dump=args.patch_debug_dump,
                    rerun=args.rerun,
                    extra_generate_args=args.extra_generate_args,
                    generate_hard_timeout_seconds=args.generate_hard_timeout_seconds,
                    generate_stall_timeout_seconds=args.generate_stall_timeout_seconds,
                )
            )
        except Exception as e:
            skip_entry: Dict[str, Any] = {"proposal_json": str(proposal_path), "error": str(e)}
            try:
                patch_name = detect_patch_name_from_proposal(proposal_path)
                out_dir = candidate_output_dir(results_dir, patch_name, args.run_suffix)
                guard_path = out_dir / "controller_guard_snapshot.json"
                if guard_path.exists():
                    skip_entry["controller_guard_snapshot"] = str(guard_path)
                    skip_entry["guard_snapshot"] = safe_load_json(guard_path)
            except Exception:
                pass
            skipped.append(skip_entry)
            print(f"!! Skipping proposal {proposal_path}: {e}")

    report = {
        "run_suffix": args.run_suffix,
        "model": args.model,
        "max_num_problems": args.max_num_problems,
        "target_pids": target_pids,
        "val_ids": val_ids,
        "baseline_results": args.baseline_results,
        "baseline_val_cards": args.baseline_val_cards,
        "generate_hard_timeout_seconds": args.generate_hard_timeout_seconds,
        "generate_stall_timeout_seconds": args.generate_stall_timeout_seconds,
        "skipped_proposals": skipped,
        "candidates": [
            {**{k: v for k, v in asdict(s).items() if k != "target_results"}, "target_results": [asdict(t) for t in s.target_results]}
            for s in summaries
        ],
    }
    write_json(report_out, report)

    print("\n== Candidate summaries ==")
    for s in summaries:
        print(f"\n[{s.candidate_name}]")
        print(f"original_patch_type: {s.original_patch_type}")
        print(f"applied_patch_type: {s.applied_patch_type}")
        print(f"recommendation: {s.recommendation}")
        print(f"rationale: {s.rationale}")
        print(f"val: {s.val_correct}/{s.num_val_cards} correct")
        print(f"val failure types: {s.val_failure_types}")
        print("target pids:")
        for t in s.target_results:
            print(
                f"  - pid {t.pid}: gt={t.gt_answer!r}, pred={t.pred_answer!r}, "
                f"base_pred={t.baseline_pred_answer!r}, est_correct={t.estimated_correct}, "
                f"base_est_correct={t.baseline_estimated_correct}, improved_vs_baseline={t.improved_vs_baseline}"
            )

    if skipped:
        print("\n== Skipped proposals ==")
        for row in skipped:
            print(f"- {row['proposal_json']}: {row['error']}")

    print(f"\nWrote round report: {report_out}")


if __name__ == "__main__":
    main()
