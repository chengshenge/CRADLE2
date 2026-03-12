from __future__ import annotations
import hashlib
import importlib.util
import inspect
import json
import os
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .registry import ensure_store, load_registry, save_registry
from .validator import validate_candidate_code


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _slug(s: str) -> str:
    out = []
    for ch in (s or ""):
        out.append(ch if ch.isalnum() else "_")
    s2 = "".join(out).strip("_").lower()
    while "__" in s2:
        s2 = s2.replace("__", "_")
    return s2 or "tool"


def inspect_tools(base_tools_module, include_dynamic: bool = True) -> Dict[str, Any]:
    return {
        "ok": True,
        "tools": base_tools_module.list_available_tools(include_dynamic=include_dynamic),
        "tool_evolve_enabled": os.environ.get("VSK_TOOL_EVOLVE_ENABLED", "0") == "1",
        "store_dir": os.environ.get("VSK_TOOL_STORE_DIR", ""),
    }


def propose_tool(name: str, description: str, code: str, store_dir: str, base_tools_module=None) -> Dict[str, Any]:
    ensure_store(store_dir)
    name = _slug(name)
    code = (code or "").strip()
    if not code:
        return {"ok": False, "error": "empty code"}
    validation = validate_candidate_code(code, expected_name=name)
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]
    candidate_id = f"{name}_{digest}"
    cpath = Path(store_dir) / "candidates" / f"{candidate_id}.py"
    cpath.write_text(code + ("\n" if not code.endswith("\n") else ""), encoding="utf-8")

    reg = load_registry(store_dir)
    reg["candidates"][candidate_id] = {
        "candidate_id": candidate_id,
        "name": name,
        "description": (description or "").strip(),
        "code_sha256_12": digest,
        "path": f"candidates/{cpath.name}",
        "created_at": _now_iso(),
        "validation": validation,
        "status": "validated" if validation.get("ok") else "invalid",
    }
    save_registry(store_dir, reg)
    return {
        "ok": bool(validation.get("ok")),
        "candidate_id": candidate_id,
        "path": str(cpath),
        "validation": validation,
        "next_step": "test_candidate_tool" if validation.get("ok") else "revise_tool_code",
    }


def _import_candidate(candidate_file: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(candidate_file))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import candidate file: {candidate_file}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pick_callable(mod, preferred_name: str | None = None):
    if preferred_name and hasattr(mod, preferred_name) and callable(getattr(mod, preferred_name)):
        return preferred_name, getattr(mod, preferred_name)
    for k in dir(mod):
        v = getattr(mod, k)
        if callable(v) and not k.startswith("_"):
            return k, v
    raise RuntimeError("no callable function found in candidate module")


def test_candidate_tool(candidate_id: str, store_dir: str, base_tools_module, image_path: str | None = None, question: str | None = None) -> Dict[str, Any]:
    reg = load_registry(store_dir)
    cand = (reg.get("candidates") or {}).get(candidate_id)
    if not isinstance(cand, dict):
        return {"ok": False, "error": f"candidate not found: {candidate_id}"}
    cpath = Path(store_dir) / cand["path"]
    if not cpath.exists():
        return {"ok": False, "error": f"candidate file missing: {cpath}"}

    try:
        mod = _import_candidate(cpath, f"_vsk_candidate_{candidate_id}")
        fn_name, fn = _pick_callable(mod, preferred_name=cand.get("name"))
        sig = inspect.signature(fn)
        kwargs = {}
        if "tools" in sig.parameters:
            kwargs["tools"] = base_tools_module
        if "question" in sig.parameters and question is not None:
            kwargs["question"] = question
        if "debug" in sig.parameters:
            kwargs["debug"] = True

        args = []
        call_notes = []
        if image_path and os.path.exists(image_path):
            from PIL import Image
            pil = Image.open(image_path).convert("RGB")
            params = list(sig.parameters.keys())
            if params:
                p0 = params[0]
                if p0 in ("image", "img", "input_image", "pil_image"):
                    args.append(pil); call_notes.append("arg0=PIL.Image")
                elif p0 in ("image_path", "img_path", "path"):
                    args.append(image_path); call_notes.append("arg0=image_path")
            if "image" in sig.parameters and not args and "image" not in kwargs:
                kwargs["image"] = pil; call_notes.append("kw image=PIL.Image")
            if "image_path" in sig.parameters and "image_path" not in kwargs:
                kwargs["image_path"] = image_path; call_notes.append("kw image_path")
        else:
            call_notes.append("no image available")
        result = fn(*args, **kwargs)
        test_out = {
            "ok": True,
            "tested_at": _now_iso(),
            "fn_name": fn_name,
            "call_notes": call_notes,
            "result_type": str(type(result)),
            "result_preview": repr(result)[:800],
        }
        cand["status"] = "tested_ok"
        cand["last_test"] = test_out
    except Exception as e:
        tb = traceback.format_exc()
        test_out = {"ok": False, "tested_at": _now_iso(), "error": str(e), "traceback": tb}
        cand["status"] = "tested_failed"
        cand["last_test"] = test_out
        (Path(store_dir) / "failed" / f"{candidate_id}.json").write_text(json.dumps(test_out, ensure_ascii=False, indent=2), encoding="utf-8")

    reg["candidates"][candidate_id] = cand
    save_registry(store_dir, reg)
    return test_out


def commit_candidate_tool(candidate_id: str, store_dir: str, base_tools_module, enable: bool = True) -> Dict[str, Any]:
    reg = load_registry(store_dir)
    cand = (reg.get("candidates") or {}).get(candidate_id)
    if not isinstance(cand, dict):
        return {"ok": False, "error": f"candidate not found: {candidate_id}"}
    if not cand.get("validation", {}).get("ok"):
        return {"ok": False, "error": "candidate validation failed; cannot commit"}

    tool_name = _slug(cand.get("name") or candidate_id)
    cur = (reg.get("tools") or {}).get(tool_name) or {}
    version = int(cur.get("version", 0)) + 1
    src = Path(store_dir) / cand["path"]
    if not src.exists():
        return {"ok": False, "error": f"candidate file missing: {src}"}
    dst = Path(store_dir) / "committed" / f"{tool_name}_v{version}.py"
    shutil.copy2(src, dst)

    meta = {
        "name": tool_name,
        "description": cand.get("description", ""),
        "module_path": f"committed/{dst.name}",
        "enabled": bool(enable),
        "version": version,
        "created_at": _now_iso(),
        "created_from_candidate": candidate_id,
        "success_count": 0,
        "fail_count": 0,
        "quality_score": 0.0,
        "dynamic": True,
        "status": "enabled" if enable else "disabled",
    }
    reg.setdefault("tools", {})[tool_name] = meta
    cand["status"] = "committed"
    cand["committed_as"] = tool_name
    cand["committed_version"] = version
    reg["candidates"][candidate_id] = cand
    save_registry(store_dir, reg)

    if enable:
        try:
            from .loader import load_enabled_tools
            load_enabled_tools(base_tools_module, store_dir)
        except Exception:
            pass

    return {"ok": True, "name": tool_name, "version": version, "module_path": str(dst), "enabled": bool(enable)}


def disable_tool(name: str, store_dir: str, base_tools_module=None) -> Dict[str, Any]:
    reg = load_registry(store_dir)
    meta = (reg.get("tools") or {}).get(name)
    if not isinstance(meta, dict):
        return {"ok": False, "error": f"tool not found: {name}"}
    meta["enabled"] = False
    meta["status"] = "disabled"
    meta["updated_at"] = _now_iso()
    reg["tools"][name] = meta
    save_registry(store_dir, reg)
    try:
        if base_tools_module is not None:
            base_tools_module.unregister_dynamic_tool(name)
    except Exception:
        pass
    return {"ok": True, "name": name, "status": "disabled"}
