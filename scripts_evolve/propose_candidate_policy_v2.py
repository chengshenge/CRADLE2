#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(obj: Any, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_policy(dominant_family: str | None, failure_summary: Dict[str, Any], round_id: int | None, parent_version: str) -> Dict[str, Any]:
    target_residuals = failure_summary.get("target_residuals", [])
    target_pids = [x["pid"] for x in target_residuals if x.get("family") == dominant_family]

    if not dominant_family:
        return {
            "protocol_version": "candidate_policy_v2",
            "round_id": round_id,
            "parent_version": parent_version,
            "candidate_type": "no_op_candidate",
            "candidate_family": "none",
            "rationale": "No dominant incorrect failure family found.",
            "target_failure_family": None,
            "target_pids": [],
            "builder_action": "skip_candidate_build",
            "expected_risks": [],
            "validation_targets": [],
        }

    mapping = {
        "comparison_relation": {
            "candidate_type": "prompt_protocol_candidate",
            "candidate_family": "relation_protocol_family",
            "builder_action": "build_relation_prompt_candidate_v1",
            "rationale": "Dominant residuals are comparison/relation style; try a constrained single-pass comparison protocol.",
            "expected_risks": [
                "Prompt may overfire on non-comparison questions",
                "May reduce flexibility on ambiguous comparison cases"
            ],
            "validation_targets": ["t2_fastval", "t2_fullval"]
        },
        "subtraction_membership": {
            "candidate_type": "prompt_protocol_candidate",
            "candidate_family": "membership_protocol_family",
            "builder_action": "build_membership_prompt_candidate_v1",
            "rationale": "Dominant residuals are subtraction/membership style; try object-by-object membership verification before subtraction.",
            "expected_risks": [
                "Longer structured output may slow inference",
                "May still fail if object grounding itself is unstable"
            ],
            "validation_targets": ["t2_fastval", "t2_fullval"]
        },
        "measurement_target": {
            "candidate_type": "tool_routing_candidate",
            "candidate_family": "measurement_routing_family",
            "builder_action": "build_measurement_routing_candidate_v1",
            "rationale": "Dominant residuals are measurement-target errors; prefer routing-level correction before generic prompt patching.",
            "expected_risks": [
                "Routing may be too broad and affect unrelated numeric questions",
                "May need later upgrade to a new dedicated tool"
            ],
            "validation_targets": ["t2_fastval", "t2_fullval"]
        },
        "counting_visibility": {
            "candidate_type": "prompt_protocol_candidate",
            "candidate_family": "counting_visibility_family",
            "builder_action": "build_counting_visibility_prompt_candidate_v1",
            "rationale": "Dominant residuals are count/visibility tasks; try a tighter count-and-filter protocol.",
            "expected_risks": [
                "May increase prompt length",
                "Visibility criteria may remain ambiguous"
            ],
            "validation_targets": ["t2_fastval", "t2_fullval"]
        },
        "tool_failure": {
            "candidate_type": "routing_candidate",
            "candidate_family": "tool_reliability_family",
            "builder_action": "build_tool_reliability_candidate_v1",
            "rationale": "Dominant residuals look like tool or runner failures; prefer routing/reliability fixes over new semantic prompt patches.",
            "expected_risks": [
                "May hide deeper semantic failures",
                "Could add latency if retries are introduced"
            ],
            "validation_targets": ["t2_fastval"]
        },
        "extraction_failure": {
            "candidate_type": "extraction_candidate",
            "candidate_family": "extraction_family",
            "builder_action": "build_extraction_candidate_v1",
            "rationale": "Dominant residuals look like answer extraction/output-format issues; improve extraction before changing reasoning.",
            "expected_risks": [
                "May improve metrics without improving actual reasoning quality"
            ],
            "validation_targets": ["t2_fastval", "t2_fullval"]
        },
        "unknown": {
            "candidate_type": "no_op_candidate",
            "candidate_family": "unknown",
            "builder_action": "human_review_or_skip",
            "rationale": "Dominant family is unknown; skip automatic build in v2.1.",
            "expected_risks": [
                "Unknown family may hide multiple mixed mechanisms"
            ],
            "validation_targets": []
        },
    }

    chosen = mapping.get(dominant_family, mapping["unknown"])

    return {
        "protocol_version": "candidate_policy_v2",
        "round_id": round_id,
        "parent_version": parent_version,
        "candidate_type": chosen["candidate_type"],
        "candidate_family": chosen["candidate_family"],
        "rationale": chosen["rationale"],
        "target_failure_family": dominant_family,
        "target_pids": target_pids,
        "builder_action": chosen["builder_action"],
        "expected_risks": chosen["expected_risks"],
        "validation_targets": chosen["validation_targets"],
        "dominant_failure_count": failure_summary.get("dominant_failure_count", 0),
        "num_incorrect": failure_summary.get("num_incorrect", 0),
    }


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--failure-summary", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--round-id", type=int, default=None)
    ap.add_argument("--parent-version", default="v_current")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    failure_summary = read_json(args.failure_summary)
    dominant_family = failure_summary.get("dominant_failure_family")

    policy = build_policy(
        dominant_family=dominant_family,
        failure_summary=failure_summary,
        round_id=args.round_id,
        parent_version=args.parent_version,
    )
    write_json(policy, args.out_json)

    print(f"Wrote candidate policy to: {args.out_json}")
    print(f"Candidate type: {policy['candidate_type']}")
    print(f"Candidate family: {policy['candidate_family']}")
    print(f"Target failure family: {policy['target_failure_family']}")
    print(f"Builder action: {policy['builder_action']}")


if __name__ == "__main__":
    main()
