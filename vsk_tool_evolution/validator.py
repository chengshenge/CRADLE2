from __future__ import annotations
import ast
from typing import Any, Dict, List

_ALLOWED_IMPORT_PREFIXES = {
    "math", "json", "typing", "collections", "statistics", "itertools",
    "numpy", "cv2", "PIL", "PIL.Image"
}
_FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "input"}
_FORBIDDEN_ATTR_ROOTS = {"os", "sys", "subprocess", "socket", "shutil", "pathlib", "requests", "urllib"}


def _is_allowed_import(name: str) -> bool:
    return any(name == p or name.startswith(p + ".") for p in _ALLOWED_IMPORT_PREFIXES)


def validate_candidate_code(code: str, expected_name: str | None = None) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"ok": False, "errors": [f"SyntaxError: {e}"], "warnings": warnings, "function_names": []}

    func_defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if not func_defs:
        errors.append("candidate code must define at least one top-level function")
    if len(func_defs) > 1:
        warnings.append("multiple top-level functions found; only the first will be used")

    if expected_name and func_defs and expected_name not in [f.name for f in func_defs]:
        warnings.append(f"expected function name '{expected_name}' not found")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if not _is_allowed_import(a.name):
                    errors.append(f"forbidden import: {a.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod and not _is_allowed_import(mod):
                errors.append(f"forbidden import: {mod}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                errors.append(f"forbidden call: {node.func.id}")
            elif isinstance(node.func, ast.Attribute):
                root = node.func.value
                if isinstance(root, ast.Name) and root.id in _FORBIDDEN_ATTR_ROOTS:
                    errors.append(f"forbidden module call: {root.id}.{node.func.attr}")

    if func_defs:
        f0 = func_defs[0]
        if not ast.get_docstring(f0):
            warnings.append("function should include a docstring")
        arg_names = [a.arg for a in f0.args.args]
        if not any(a in arg_names for a in ("image", "image_path", "img", "input_image")):
            warnings.append("function may not accept an image argument")
    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings, "function_names": [f.name for f in func_defs]}
