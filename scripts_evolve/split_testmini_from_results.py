#!/usr/bin/env python3
import argparse
import json
import random
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-file", required=True, help="Path to results/out.json")
    ap.add_argument("--out-json", required=True, help="Output split json")
    ap.add_argument("--val-ratio", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    results = json.load(open(args.results_file, "r", encoding="utf-8"))
    pids = sorted(results.keys(), key=lambda x: int(x) if str(x).isdigit() else str(x))

    rng = random.Random(args.seed)
    pids_shuf = pids[:]
    rng.shuffle(pids_shuf)

    n_val = max(1, int(round(len(pids_shuf) * args.val_ratio))) if pids_shuf else 0
    val = sorted(pids_shuf[:n_val], key=lambda x: int(x) if str(x).isdigit() else str(x))
    discovery = sorted(pids_shuf[n_val:], key=lambda x: int(x) if str(x).isdigit() else str(x))

    out = {
        "seed": args.seed,
        "val_ratio": args.val_ratio,
        "num_total": len(pids),
        "num_discovery": len(discovery),
        "num_val": len(val),
        "discovery": discovery,
        "val": val,
    }

    out_path = Path(args.out_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"wrote {out_path}")
    print(f"discovery={len(discovery)} val={len(val)} total={len(pids)}")


if __name__ == "__main__":
    main()
