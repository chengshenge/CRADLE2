from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict


def ensure_store(store_dir: str) -> Path:
    root = Path(store_dir)
    (root / "candidates").mkdir(parents=True, exist_ok=True)
    (root / "committed").mkdir(parents=True, exist_ok=True)
    (root / "failed").mkdir(parents=True, exist_ok=True)
    reg = root / "registry.json"
    if not reg.exists():
        reg.write_text(json.dumps({"version": 1, "tools": {}, "candidates": {}}, indent=2), encoding="utf-8")
    return root


def registry_path(store_dir: str) -> Path:
    return ensure_store(store_dir) / "registry.json"


def load_registry(store_dir: str) -> Dict[str, Any]:
    p = registry_path(store_dir)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("registry root must be object")
    except Exception:
        data = {"version": 1, "tools": {}, "candidates": {}}
    data.setdefault("version", 1)
    data.setdefault("tools", {})
    data.setdefault("candidates", {})
    return data


def save_registry(store_dir: str, data: Dict[str, Any]) -> Path:
    p = registry_path(store_dir)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return p
