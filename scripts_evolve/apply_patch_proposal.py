#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


TYPE_TO_BUCKET = {
    "prompt": "prompts",
    "tool": "tools",
    "policy": "policies",
    "extractor": "extractors",
    "verifier": "verifiers",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposal-json", required=True, help="Patch proposal json")
    ap.add_argument("--patch-root", required=True, help="generated_patches root")
    ap.add_argument("--base-config", required=True, help="Base active_patches.json")
    ap.add_argument("--out-config", required=True, help="Candidate active_patches json")
    args = ap.parse_args()

    proposal = json.load(open(args.proposal_json, "r", encoding="utf-8"))
    base_cfg = json.load(open(args.base_config, "r", encoding="utf-8"))

    patch_type = str(proposal["patch_type"]).strip().lower()
    if patch_type not in TYPE_TO_BUCKET:
        raise ValueError(f"unsupported patch_type: {patch_type}")

    relpath = proposal["relpath"]
    content = proposal["content"]
    enable = bool(proposal.get("enable", True))

    patch_root = Path(args.patch_root)
    dst = patch_root / relpath
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(content)

    bucket = TYPE_TO_BUCKET[patch_type]
    enabled = base_cfg.setdefault("enabled", {})
    enabled.setdefault(bucket, [])

    if enable and relpath not in enabled[bucket]:
        enabled[bucket].append(relpath)

    out_cfg = Path(args.out_config)
    out_cfg.parent.mkdir(parents=True, exist_ok=True)
    with open(out_cfg, "w", encoding="utf-8") as f:
        json.dump(base_cfg, f, ensure_ascii=False, indent=2)

    print(f"wrote patch file: {dst}")
    print(f"wrote candidate config: {out_cfg}")


if __name__ == "__main__":
    main()
