#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


NUM_RE = re.compile(r'[-+]?\d+(?:\.\d+)?')
LETTER_RE = re.compile(r'\(([A-E])\)|\b([A-E])\b', re.IGNORECASE)


def qdir_from_pid(traces_root: Path, pid: str) -> Path:
    try:
        return traces_root / f"q{int(pid):04d}"
    except Exception:
        return traces_root / f"q{pid}"


def normalize_text(s: Any) -> str:
    return "" if s is None else str(s).strip()


def extract_prediction(record: Dict[str, Any]) -> Optional[str]:
    resp = normalize_text(record.get("response"))
    if not resp:
        return None

    if "ANSWER:" in resp:
        tail = resp.split("ANSWER:")[-1].strip()
    else:
        tail = resp.strip()

    answer_type = normalize_text(record.get("answer_type")).lower()
    question_type = normalize_text(record.get("question_type")).lower()

    if question_type == "multi_choice":
        m = LETTER_RE.search(tail)
        if m:
            return (m.group(1) or m.group(2)).upper()

    if answer_type in {"float", "integer"}:
        m = NUM_RE.search(tail)
        if m:
            return m.group(0)

    m = NUM_RE.search(tail)
    if m:
        return m.group(0)

    m = LETTER_RE.search(tail)
    if m:
        return (m.group(1) or m.group(2)).upper()

    return tail[:200] if tail else None


def compare_answer(pred: Optional[str], gt: Any) -> Optional[bool]:
    if pred is None:
        return None

    gt_s = normalize_text(gt)
    if not gt_s:
        return None

    try:
        p = float(pred)
        g = float(gt_s)
        return abs(p - g) < 1e-6
    except Exception:
        pass

    return pred.strip().upper() == gt_s.strip().upper()


def trace_files_summary(qdir: Path) -> List[str]:
    if not qdir.exists():
        return []
    return sorted([p.name for p in qdir.iterdir() if p.is_file()])


def infer_failure_type(record: Dict[str, Any], pred: Optional[str], is_correct: Optional[bool], trace_files: List[str]) -> str:
    resp = normalize_text(record.get("response"))

    if "Response Error" in resp:
        return "api_error"
    if any("exception" in x.lower() or "error" in x.lower() for x in trace_files):
        return "runtime_or_tool_error"
    if not resp:
        return "empty_response"
    if pred is None:
        return "format_or_extraction_issue"
    if is_correct is True:
        return "correct"
    return "reasoning_or_perception_error"


def make_card(pid: str, record: Dict[str, Any], qdir: Path) -> Dict[str, Any]:
    pred = extract_prediction(record)
    gt = record.get("answer")
    is_correct = compare_answer(pred, gt)
    trace_files = trace_files_summary(qdir)
    response = normalize_text(record.get("response"))

    action_count = len(re.findall(r"\bACTION\s+\d+\b", response))
    thought_count = len(re.findall(r"\bTHOUGHT\s+\d+\b", response))

    return {
        "pid": pid,
        "qdir": str(qdir),
        "question_type": record.get("question_type"),
        "answer_type": record.get("answer_type"),
        "category": ((record.get("metadata") or {}).get("category")),
        "context": ((record.get("metadata") or {}).get("context")),
        "skills": ((record.get("metadata") or {}).get("skills")),
        "gt_answer": gt,
        "pred_answer": pred,
        "is_correct": is_correct,
        "initial_failure_type": infer_failure_type(record, pred, is_correct, trace_files),
        "precision_hint": record.get("precision"),
        "response_preview": response[:1200],
        "question_preview": normalize_text(record.get("question"))[:800],
        "trace_files": trace_files,
        "thought_count": thought_count,
        "action_count": action_count,
        "backbone_model": record.get("backbone_model"),
        "agent": record.get("agent"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-file", required=True)
    ap.add_argument("--traces-root", required=True)
    ap.add_argument("--out-jsonl", required=True)
    ap.add_argument("--split-json", default=None)
    ap.add_argument("--split-key", default="discovery", choices=["discovery", "val"])
    args = ap.parse_args()

    results = json.load(open(args.results_file, "r", encoding="utf-8"))
    traces_root = Path(args.traces_root)

    selected_pids = None
    if args.split_json:
        split = json.load(open(args.split_json, "r", encoding="utf-8"))
        selected_pids = set(split[args.split_key])

    out_path = Path(args.out_jsonl)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for pid, rec in sorted(results.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else str(kv[0])):
            if selected_pids is not None and pid not in selected_pids:
                continue
            qdir = qdir_from_pid(traces_root, pid)
            card = make_card(pid, rec, qdir)
            f.write(json.dumps(card, ensure_ascii=False) + "\n")
            count += 1

    print(f"wrote {out_path} ({count} cards)")


if __name__ == "__main__":
    main()
