from __future__ import annotations
from typing import Any, Dict, List, Literal, TypedDict

PatchType = Literal["prompt", "tool", "policy", "extractor", "verifier"]

class PatchFile(TypedDict, total=False):
    relpath: str
    content: str

class PatchProposal(TypedDict, total=False):
    patch_id: str
    patch_type: PatchType
    target_failure_cluster: str
    files: List[PatchFile]
    enable_by_default: bool
    expected_benefit: str
    risk: str
    acceptance_criteria: Dict[str, Any]

class ActivePatches(TypedDict, total=False):
    version: int
    enabled: Dict[str, List[str]]
