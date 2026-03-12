#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(obj: Any, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_text(text: str, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def safe_remove(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def symlink_or_copy_repo(base_repo_root: Path, candidate_repo_root: Path, materialized_top_levels: List[str]) -> None:
    """
    Create a lightweight candidate repo:
    - most top-level entries are symlinked
    - selected mutable top-level dirs/files are copied physically so they can be patched
    """
    candidate_repo_root.mkdir(parents=True, exist_ok=True)

    ignored_names = {
        ".git",
        ".pytest_cache",
        ".mypy_cache",
        "__pycache__",
        "results",
    }

    for item in base_repo_root.iterdir():
        name = item.name
        if name in ignored_names:
            continue

        dst = candidate_repo_root / name
        safe_remove(dst)

        if name in materialized_top_levels:
            if item.is_dir():
                shutil.copytree(item, dst, symlinks=False)
            else:
                shutil.copy2(item, dst)
        else:
            os.symlink(item.resolve(), dst, target_is_directory=item.is_dir())


def apply_extraction_candidate_v1(candidate_repo_root: Path) -> Dict[str, Any]:
    """
    Patch evaluation/extract_answer.py so quick extraction handles:
    1) multiline 'ANSWER: ...' anywhere in the response
    2) multi-choice letter extraction more robustly

    This is intentionally minimal and only targets quick_extract.
    """
    target = candidate_repo_root / "evaluation" / "extract_answer.py"
    text = target.read_text(encoding="utf-8")

    old_answer_block = '''    # Answer: XXX
    try:
        m = re.search(r"(?i)^answer\\s*[:：]\\s*([^\\n\\r]+)", response)
        if m:
            return m.group(1).strip().strip(".")
    except Exception:
        pass
'''

    new_answer_block = '''    # Answer: XXX (allow multiline final ANSWER lines)
    try:
        matches = re.findall(r"(?im)^\\s*answer\\s*[:：]\\s*([^\\n\\r]+)", response)
        if matches:
            last = matches[-1].strip().strip(".")
            if answer_type == "integer":
                nums = re.findall(r"-?\\d+", last)
                if nums:
                    return nums[-1]
            if answer_type == "float":
                nums = re.findall(r"-?\\d+(?:\\.\\d+)?", last)
                if nums:
                    return nums[-1]
            return last
    except Exception:
        pass
'''

    if old_answer_block not in text:
        raise RuntimeError("Could not find multiline ANSWER extraction anchor in extract_answer.py")

    text = text.replace(old_answer_block, new_answer_block, 1)

    old_multichoice_block = '''    # If multi-choice: try to find a single letter choice (A/B/C/D/...)
    if question_type == "multi_choice" and choices:
        # e.g., "Answer: C" or "Option C"
        try:
            m = re.search(r"(?i)\\b([A-Z])\\b", response)
            if m and m.group(1) in choices:
                return m.group(1)
        except Exception:
            pass
'''

    new_multichoice_block = '''    # If multi-choice: try to find an explicit answer letter
    if question_type == "multi_choice" and choices:
        try:
            m = re.search(r"(?im)^\\s*answer\\s*[:：]\\s*([A-Z])\\b", response)
            if m:
                return m.group(1).upper()
        except Exception:
            pass

        try:
            m = re.search(r"(?i)\\boption\\s*([A-Z])\\b", response)
            if m:
                return m.group(1).upper()
        except Exception:
            pass

        try:
            m = re.search(r"(?i)\\(([A-Z])\\)", response)
            if m:
                return m.group(1).upper()
        except Exception:
            pass
'''

    if old_multichoice_block not in text:
        raise RuntimeError("Could not find multi-choice extraction anchor in extract_answer.py")

    text = text.replace(old_multichoice_block, new_multichoice_block, 1)

    target.write_text(text, encoding="utf-8")

    return {
        "modified_files": ["evaluation/extract_answer.py"],
        "summary": "Patched quick_extract to parse multiline ANSWER lines and robust multi-choice letters.",
    }


def build_candidate(policy: Dict[str, Any], base_repo_root: Path, out_root: Path, round_id: int | None,
                    parent_version: str, base_patch_config: str) -> Dict[str, Any]:
    candidate_type = policy.get("candidate_type")
    builder_action = policy.get("builder_action")
    candidate_family = policy.get("candidate_family", "unknown_family")

    round_tag = f"round_{int(round_id):03d}" if round_id is not None else "round_unknown"
    candidate_version = f"{parent_version}__{candidate_family}__{round_tag}"

    bundle_root = out_root / candidate_version
    candidate_repo_root = bundle_root / "repo"
    manifest_path = bundle_root / "candidate_manifest.json"
    summary_md_path = bundle_root / "summary.md"

    safe_remove(bundle_root)
    bundle_root.mkdir(parents=True, exist_ok=True)

    # For v2.2 we only support extraction candidate build concretely.
    materialized_top_levels = ["evaluation"]

    symlink_or_copy_repo(
        base_repo_root=base_repo_root,
        candidate_repo_root=candidate_repo_root,
        materialized_top_levels=materialized_top_levels,
    )

    build_result = {
        "modified_files": [],
        "summary": "No-op build",
    }

    if builder_action == "build_extraction_candidate_v1":
        build_result = apply_extraction_candidate_v1(candidate_repo_root)
    else:
        # In v2.2, other candidate families are not yet auto-built.
        build_result = {
            "modified_files": [],
            "summary": f"No concrete builder implemented yet for {builder_action}.",
        }

    manifest = {
        "protocol_version": "candidate_builder_v2",
        "round_id": round_id,
        "parent_version": parent_version,
        "candidate_version": candidate_version,
        "candidate_type": candidate_type,
        "candidate_family": candidate_family,
        "builder_action": builder_action,
        "target_failure_family": policy.get("target_failure_family"),
        "target_pids": policy.get("target_pids", []),
        "rationale": policy.get("rationale"),
        "expected_risks": policy.get("expected_risks", []),
        "validation_targets": policy.get("validation_targets", []),
        "base_repo_root": str(base_repo_root.resolve()),
        "candidate_repo_root": str(candidate_repo_root.resolve()),
        "candidate_patch_config": base_patch_config,
        "build_result": build_result,
    }
    write_json(manifest, manifest_path)

    summary_lines = [
        f"# Candidate Build Summary",
        "",
        f"- Parent version: `{parent_version}`",
        f"- Candidate version: `{candidate_version}`",
        f"- Candidate type: `{candidate_type}`",
        f"- Candidate family: `{candidate_family}`",
        f"- Builder action: `{builder_action}`",
        f"- Candidate repo root: `{candidate_repo_root.resolve()}`",
        f"- Candidate patch config: `{base_patch_config}`",
        "",
        "## Rationale",
        policy.get("rationale", ""),
        "",
        "## Modified files",
    ]
    for x in build_result.get("modified_files", []):
        summary_lines.append(f"- `{x}`")
    summary_lines.append("")
    summary_lines.append("## Build summary")
    summary_lines.append(build_result.get("summary", ""))

    write_text("\n".join(summary_lines) + "\n", summary_md_path)

    return manifest


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-json", required=True)
    ap.add_argument("--base-repo-root", required=True)
    ap.add_argument("--out-root", default="generated_candidates")
    ap.add_argument("--round-id", type=int, default=None)
    ap.add_argument("--parent-version", default="v_current")
    ap.add_argument(
        "--base-patch-config",
        default="configs/active_patches.accepted_relation_compare_single_pass_v1.clean.json"
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    policy = read_json(args.policy_json)
    base_repo_root = Path(args.base_repo_root).resolve()
    out_root = Path(args.out_root).resolve()

    manifest = build_candidate(
        policy=policy,
        base_repo_root=base_repo_root,
        out_root=out_root,
        round_id=args.round_id,
        parent_version=args.parent_version,
        base_patch_config=args.base_patch_config,
    )

    print("Candidate build complete.")
    print(f"Candidate version: {manifest['candidate_version']}")
    print(f"Candidate repo root: {manifest['candidate_repo_root']}")
    print(f"Candidate patch config: {manifest['candidate_patch_config']}")
    print(f"Builder action: {manifest['builder_action']}")


if __name__ == "__main__":
    main()
