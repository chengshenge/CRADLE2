#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(obj: Any, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalize_text(x: Any) -> str:
    if x is None:
        return ""
    return str(x).strip()


def get_question_text(rec: Dict[str, Any]) -> str:
    for key in ["question", "query", "question_preview", "prompt"]:
        val = rec.get(key)
        if val:
            return normalize_text(val)
    return ""


def is_tool_failure(rec: Dict[str, Any]) -> bool:
    response = normalize_text(rec.get("response"))
    error = normalize_text(rec.get("error"))
    text = (response + "\n" + error).lower()
    tool_failure_markers = [
        "response error",
        "timed out",
        "timeout",
        "output.json not found",
        "visualsketchpad failed",
        "subprocess",
        "traceback",
        "connection refused",
        "failed to",
        "error code:",
    ]
    return any(m in text for m in tool_failure_markers)


def is_extraction_failure(rec: Dict[str, Any]) -> bool:
    extraction = rec.get("extraction")
    prediction = rec.get("prediction")
    true_false = rec.get("true_false", None)

    if true_false is True:
        return False

    extraction_empty = normalize_text(extraction) == ""
    prediction_empty = prediction is None or normalize_text(prediction) == ""

    return extraction_empty or prediction_empty


def classify_question_family(question_text: str, rec: Dict[str, Any]) -> Tuple[str, str]:
    """
    Returns:
      family, likely_mechanism
    """
    q = question_text.lower()

    if is_tool_failure(rec):
        return "tool_failure", "tool_invocation_or_runner_failure"

    # subtraction / remove / remaining style
    if any(k in q for k in ["subtract", "remove", "left", "remaining", "remain", "exclude"]):
        return "subtraction_membership", "object_set_membership_or_attribute_verification"

    # measurement / capacity / max scale style
    if any(k in q for k in [
        "highest amount",
        "measures",
        "capacity",
        "maximum amount",
        "max amount",
        "maximum value",
        "highest value",
        "how much does",
        "scale",
        "beaker",
        "glass measures",
    ]):
        return "measurement_target", "read_max_scale_or_capacity_target"

    # comparison / relation style
    if any(k in q for k in [
        "fewer",
        "more than",
        "less than",
        "greater than",
        "larger",
        "smaller",
        "same as",
        "equal to",
        "compared to",
        "more ",
        "less ",
    ]):
        return "comparison_relation", "comparison_grounding_or_relation_choice"

    # counting / visibility / fractions
    if any(k in q for k in [
        "how many",
        "fraction",
        "percent",
        "percentage",
        "facing the camera",
        "visible",
        "wearing",
        "holding",
    ]):
        return "counting_visibility", "count_or_visibility_filtering"

    if is_extraction_failure(rec):
        return "extraction_failure", "answer_extraction_or_output_format"

    return "unknown", "unknown"


def make_problem_summary(pid: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    question_text = get_question_text(rec)
    family, mechanism = classify_question_family(question_text, rec)

    return {
        "pid": str(pid),
        "question_text": question_text,
        "answer": rec.get("answer"),
        "prediction": rec.get("prediction"),
        "extraction": rec.get("extraction"),
        "true_false": rec.get("true_false"),
        "family": family,
        "likely_mechanism": mechanism,
        "response_preview": normalize_text(rec.get("response"))[:500],
    }


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-file", required=True, help="Path to out.json after calculate_score has updated it.")
    ap.add_argument("--out-json", required=True, help="Where to write failure summary json.")
    ap.add_argument("--top-k", type=int, default=10, help="Number of top incorrect residuals to keep in compact list.")
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    results = read_json(args.results_file)

    all_items: List[Dict[str, Any]] = []
    incorrect_items: List[Dict[str, Any]] = []

    family_counts = Counter()
    incorrect_family_counts = Counter()

    total = 0
    correct = 0

    for pid, rec in results.items():
        summary = make_problem_summary(str(pid), rec)
        all_items.append(summary)

        total += 1
        if rec.get("true_false") is True:
            correct += 1
        else:
            incorrect_items.append(summary)

        family_counts[summary["family"]] += 1
        if rec.get("true_false") is not True:
            incorrect_family_counts[summary["family"]] += 1

    accuracy = (correct / total) if total else 0.0

    dominant_failure_family = None
    dominant_failure_count = 0
    if incorrect_family_counts:
        dominant_failure_family, dominant_failure_count = incorrect_family_counts.most_common(1)[0]

    target_residuals = []
    for item in incorrect_items[: args.top_k]:
        target_residuals.append({
            "pid": item["pid"],
            "family": item["family"],
            "likely_mechanism": item["likely_mechanism"],
            "question_text": item["question_text"],
            "prediction": item.get("prediction"),
            "answer": item.get("answer"),
        })

    out = {
        "protocol_version": "failure_analysis_v2",
        "results_file": str(Path(args.results_file).resolve()),
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "num_incorrect": len(incorrect_items),
        "family_counts_all": dict(family_counts),
        "family_counts_incorrect": dict(incorrect_family_counts),
        "dominant_failure_family": dominant_failure_family,
        "dominant_failure_count": dominant_failure_count,
        "target_residuals": target_residuals,
        "incorrect_problem_summaries": incorrect_items,
    }

    write_json(out, args.out_json)

    print(f"Wrote failure summary to: {args.out_json}")
    print(f"Total: {total}")
    print(f"Correct: {correct}")
    print(f"Accuracy: {accuracy:.4f}")
    print(f"Incorrect: {len(incorrect_items)}")
    print(f"Dominant failure family: {dominant_failure_family} ({dominant_failure_count})")


if __name__ == "__main__":
    main()
