from __future__ import annotations
import importlib.util
from pathlib import Path
from typing import List

from .registry import load_registry, ensure_store


def _load_module(module_path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot create import spec for {module_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_enabled_tools(base_tools_module, store_dir: str) -> List[str]:
    ensure_store(store_dir)
    reg = load_registry(store_dir)
    loaded = []
    for name, meta in (reg.get("tools") or {}).items():
        if not isinstance(meta, dict) or not meta.get("enabled", False):
            continue
        rel = meta.get("module_path")
        if not rel:
            continue
        p = Path(store_dir) / rel
        if not p.exists():
            continue
        mod = _load_module(p, f"_vsk_dynamic_tool_{name}")
        fn = getattr(mod, name, None)
        if fn is None or not callable(fn):
            for k in dir(mod):
                v = getattr(mod, k)
                if callable(v) and not k.startswith("_"):
                    fn = v
                    name = k
                    break
        if fn is None:
            continue
        md = dict(meta)
        md["loaded_from"] = str(p)
        base_tools_module.register_dynamic_tool(name, fn, md)
        loaded.append(name)
    return loaded
