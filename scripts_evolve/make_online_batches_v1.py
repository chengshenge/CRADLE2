#!/usr/bin/env python3
"""
Create online batch-evolution splits for MathVista.

Design:
- Each round uses two sequential blocks of size `batch_size`:
    T1 = first batch_size pids
    T2 = next  batch_size pids
- T1 is split into:
    - T1-discovery
    - T1-probe
- T2 is split into:
    - T2-fastval
    - T2-fullval

This script writes a single JSON plan file that future round scripts can consume.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import List, Dict, Any

from datasets import load_dataset


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset-name", default="AI4Math/MathVista")
    ap.add_argument("--test-split-name", default="testmini")
    ap.add_argument("--batch-size", type=int, default=100, help="Size of T1 and also size of T2.")
    ap.add_argument("--t1-discovery-size", type=int, default=80)
    ap.add_argument("--t1-probe-size", type=int, default=20)
    ap.add_argument("--t2-fastval-size", type=int, default=20)
    ap.add_argument("--t2-fullval-size", type=int, default=80)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--shuffle-within-batch", action="store_true",
                    help="Deterministically shuffle pids inside each T1/T2 block before splitting.")
    ap.add_argument("--out-json", required=True)
    return ap.parse_args()


def ensure_sizes(args: argparse.Namespace) -> None:
    if args.t1_discovery_size + args.t1_probe_size != args.batch_size:
        raise ValueError("t1-discovery-size + t1-probe-size must equal batch-size")
    if args.t2_fastval_size + args.t2_fullval_size != args.batch_size:
        raise ValueError("t2-fastval-size + t2-fullval-size must equal batch-size")


def chunk_rounds(pids: List[str], batch_size: int) -> List[Dict[str, List[str]]]:
    """
    Build rounds of:
      T1 = pids[i : i+batch_size]
      T2 = pids[i+batch_size : i+2*batch_size]
    Only full rounds are kept.
    """
    rounds = []
    stride = 2 * batch_size
    round_id = 1
    for start in range(0, len(pids), stride):
        t1 = pids[start:start + batch_size]
        t2 = pids[start + batch_size:start + 2 * batch_size]
        if len(t1) < batch_size or len(t2) < batch_size:
            break
        rounds.append({
            "round_id": round_id,
            "t1_all": list(t1),
            "t2_all": list(t2),
        })
        round_id += 1
    return rounds


def maybe_shuffle(items: List[str], seed: int, enable: bool) -> List[str]:
    items = list(items)
    if not enable:
        return items
    rng = random.Random(seed)
    rng.shuffle(items)
    return items


def split_block(items: List[str], a_size: int, a_name: str, b_size: int, b_name: str) -> Dict[str, List[str]]:
    if len(items) != a_size + b_size:
        raise ValueError(f"split size mismatch for block with len={len(items)}")
    return {
        a_name: list(items[:a_size]),
        b_name: list(items[a_size:a_size + b_size]),
    }


def main() -> None:
    args = parse_args()
    ensure_sizes(args)

    ds = load_dataset(args.dataset_name, split=args.test_split_name)
    pids = [str(x["pid"]) for x in ds]

    base_rounds = chunk_rounds(pids, args.batch_size)

    rounds: List[Dict[str, Any]] = []
    for r in base_rounds:
        rid = r["round_id"]
        t1_all = maybe_shuffle(r["t1_all"], seed=args.seed + rid * 10 + 1, enable=args.shuffle_within_batch)
        t2_all = maybe_shuffle(r["t2_all"], seed=args.seed + rid * 10 + 2, enable=args.shuffle_within_batch)

        t1_split = split_block(
            t1_all,
            args.t1_discovery_size,
            "t1_discovery",
            args.t1_probe_size,
            "t1_probe",
        )
        t2_split = split_block(
            t2_all,
            args.t2_fastval_size,
            "t2_fastval",
            args.t2_fullval_size,
            "t2_fullval",
        )

        rounds.append({
            "round_id": rid,
            "t1_all": t1_all,
            "t1_discovery": t1_split["t1_discovery"],
            "t1_probe": t1_split["t1_probe"],
            "t2_all": t2_all,
            "t2_fastval": t2_split["t2_fastval"],
            "t2_fullval": t2_split["t2_fullval"],
        })

    out = {
        "protocol_version": "online_batch_evolution_v1",
        "dataset_name": args.dataset_name,
        "test_split_name": args.test_split_name,
        "seed": args.seed,
        "shuffle_within_batch": args.shuffle_within_batch,
        "batch_size": args.batch_size,
        "t1_discovery_size": args.t1_discovery_size,
        "t1_probe_size": args.t1_probe_size,
        "t2_fastval_size": args.t2_fastval_size,
        "t2_fullval_size": args.t2_fullval_size,
        "num_dataset_pids": len(pids),
        "num_rounds": len(rounds),
        "rounds": rounds,
    }

    out_path = Path(args.out_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote plan to: {out_path}")
    print(f"Num dataset pids: {len(pids)}")
    print(f"Num rounds: {len(rounds)}")
    if rounds:
        print("Round 1 preview:")
        print(json.dumps(rounds[0], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
