"""Explicit, local correction learning. Never infer rules from another app's edits."""
from __future__ import annotations

from difflib import SequenceMatcher
import re


def correction_candidates(before: str, after: str) -> list[tuple[str, str]]:
    candidates = []
    for tag, a, b, c, d in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag != "replace":
            continue
        old, new = before[a:b].strip(), after[c:d].strip()
        if not old or not new or not any(ch.isalnum() for ch in old) or not any(ch.isalnum() for ch in new):
            continue
        # English replacements should cover complete tokens, e.g. cloude -> claude.
        while a > 0 and c > 0 and re.fullmatch(r"[A-Za-z0-9_]", before[a-1]) and before[a-1] == after[c-1]:
            a -= 1; c -= 1
        while b < len(before) and d < len(after) and re.fullmatch(r"[A-Za-z0-9_]", before[b]) and before[b] == after[d]:
            b += 1; d += 1
        pair = (before[a:b].strip(), after[c:d].strip())
        if pair not in candidates:
            candidates.append(pair)
    return candidates


def validate_rule(old: str, new: str) -> tuple[str, str]:
    old, new = old.strip(), new.strip()
    if len(old) < 2:
        raise ValueError("错词至少两个字符；单字差异请补充完整词语。")
    if not new:
        raise ValueError("正确词不能为空；删除文字不作为纠错规则。")
    if old == new:
        raise ValueError("原词和正确词相同，无需记住。")
    return old, new


def merge_rule_edits(current, baseline, desired):
    """Three-way merge: untouched old rows must not overwrite newer explicit decisions."""
    merged = dict(current)
    for key in baseline.keys() | desired.keys():
        previous, wanted = baseline.get(key), desired.get(key)
        if wanted == previous:
            continue
        if current.get(key) not in (previous, wanted):
            raise ValueError(f"“{key}”的规则已在另一窗口修改，请重新打开设置后再编辑。")
        if wanted is None:
            merged.pop(key, None)
        else:
            key, wanted = validate_rule(key, wanted)
            merged[key] = wanted
    return merged
