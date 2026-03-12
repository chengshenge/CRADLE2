from __future__ import annotations
import importlib.util
import json
import os
import types
from pathlib import Path
from typing import Any, Dict, List


def cradle_root() -> Path:
    env = os.environ.get("VSK_CRADLE_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[1]


def resolve_path(p: str) -> Path:
    p = str(p or "").strip()
    if not p:
        return Path()
    path = Path(p)
    if path.is_absolute():
        return path
    return (cradle_root() / path).resolve()


def load_active_patches(patch_config: str | None = None) -> Dict[str, List[str]]:
    cfg = resolve_path(patch_config or os.environ.get("VSK_PATCH_CONFIG", "configs/active_patches.json"))
    enabled = {"prompts": [], "tools": [], "policies": [], "extractors": [], "verifiers": []}
    if not cfg.exists():
        return enabled
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return enabled
    if isinstance(data, dict):
        e = data.get("enabled", {})
        if isinstance(e, dict):
            for k in enabled:
                v = e.get(k, [])
                if isinstance(v, list):
                    enabled[k] = [str(x) for x in v]
    return enabled


_MEASUREMENT_HINTS = (
    "measuring cup", "beaker", "capacity", "fill level", "highest amount", "what is the total volume",
    "how much liquid", "graduated", "scale", "ml", "milliliter", "gram", "grams", "unit: g",
)
_COMPARE_HINTS = (
    " greater ", " fewer ", " more ", " less ", " left of ", " right of ", " behind ", " in front of ",
    "are there fewer", "is the number of", "is there more", "is there less",
)
_SUBTRACTION_HINTS = (
    "subtract", "remove", "how many objects are left", "how many are left", "objects are left",
    "remaining", "left after", "left?",
)


def _question_text(base_prompt: str) -> str:
    q = os.environ.get("VSK_QUESTION_TEXT", "").strip()
    return q if q else str(base_prompt or "")


def _classify_question(text: str) -> str:
    q = f" {str(text or '').strip().lower()} "
    if not q.strip():
        return "unknown"
    if any(k in q for k in _MEASUREMENT_HINTS):
        return "measurement_target"
    if any(k in q for k in _COMPARE_HINTS):
        return "comparison_relation"
    if any(k in q for k in _SUBTRACTION_HINTS):
        return "set_counting_or_dedup"
    return "unknown"


def _hook_prompt(mode: str) -> str:
    if mode == "set_subtraction_single_pass":
        return (
            "For subtraction / remove / remaining-count questions only, follow this exact single-pass workflow before the final answer:\n"
            "1. Make a short grounded list of the visible physical objects using brief descriptive references based on appearance or position.\n"
            "2. For each subtraction condition, list which grounded objects satisfy it.\n"
            "3. Compute the removed set as the union of those grounded objects, counting each physical object at most once even if it satisfies multiple conditions.\n"
            "4. Compute the remaining set explicitly from the visible set minus the removed union.\n"
            "5. State the remaining grounded object references or the remaining count only after the remaining set has been formed.\n"
            "Do not invent abstract object IDs. Do not recount from scratch after the remaining set is formed. Do not switch this workflow to comparison or measurement questions."
        )
    return ""


def _maybe_dump_prompt(final_prompt: str):
    dump_path = os.environ.get("VSK_PROMPT_DUMP_PATH", "").strip()
    if not dump_path:
        return
    try:
        Path(dump_path).write_text(final_prompt, encoding="utf-8")
    except Exception:
        pass


def _dump_hook_meta(payload: dict):
    task_dir = os.environ.get("VSK_TASK_DIR", "").strip()
    if not task_dir:
        return
    try:
        p = Path(task_dir) / "execution_hook_applied.json"
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def apply_prompt_patches(base_prompt: str, patch_root: str | None = None, enabled_prompts: List[str] | None = None) -> str:
    if not isinstance(base_prompt, str):
        return base_prompt
    root = resolve_path(patch_root or os.environ.get("VSK_PATCH_ROOT", "generated_patches"))
    enabled = enabled_prompts if enabled_prompts is not None else load_active_patches().get("prompts", [])
    parts = [base_prompt]
    for rel in enabled:
        p = (root / rel).resolve()
        if p.exists() and p.is_file():
            try:
                txt = p.read_text(encoding="utf-8").strip()
                if txt:
                    parts.append(f"\n\n[PATCH_PROMPT:{rel}]\n{txt}\n")
            except Exception:
                continue

    qtext = _question_text(base_prompt)
    qcat = _classify_question(qtext)
    hook_mode = os.environ.get("VSK_EXECUTION_HOOK_MODE", "").strip()
    hook_added = False

    if hook_mode == "set_subtraction_single_pass" and qcat == "set_counting_or_dedup":
        hook_txt = _hook_prompt(hook_mode).strip()
        if hook_txt:
            parts.append(f"\n\n[EXECUTION_HOOK:{hook_mode}]\n{hook_txt}\n")
            hook_added = True

    final_prompt = "".join(parts)
    _maybe_dump_prompt(final_prompt)
    _dump_hook_meta({
        "hook_mode": hook_mode,
        "question_category": qcat,
        "hook_added": hook_added,
        "question_preview": qtext[:500],
    })
    return final_prompt


def _import_module_from_file(module_name: str, file_path: Path) -> types.ModuleType | None:
    try:
        spec = importlib.util.spec_from_file_location(module_name, str(file_path))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]
        return mod
    except Exception:
        return None


def register_tool_patches(tools_module: Any, patch_root: str | None = None, enabled_tools: List[str] | None = None) -> List[str]:
    root = resolve_path(patch_root or os.environ.get("VSK_PATCH_ROOT", "generated_patches"))
    enabled = enabled_tools if enabled_tools is not None else load_active_patches().get("tools", [])

    reg_dyn = tools_module.get("register_dynamic_tool") if isinstance(tools_module, dict) else getattr(tools_module, "register_dynamic_tool", None)
    if not callable(reg_dyn):
        return []

    loaded: List[str] = []
    for rel in enabled:
        p = (root / rel).resolve()
        if not (p.exists() and p.is_file()):
            continue
        mod = _import_module_from_file(f"vsk_patch_{p.stem}", p)
        if mod is None:
            continue
        reg = getattr(mod, "register", None)
        if callable(reg):
            try:
                reg(tools_module)
                loaded.append(rel)
                continue
            except Exception:
                pass
        try:
            for name, obj in vars(mod).items():
                if name.startswith("_") or name == "register":
                    continue
                if callable(obj):
                    meta = {"name": name, "source": "patch", "path": str(p)}
                    try:
                        reg_dyn(name, obj, meta=meta, overwrite=True)
                    except Exception:
                        pass
            loaded.append(rel)
        except Exception:
            continue
    return loaded
