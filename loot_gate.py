"""Candidate result validation and submission controls for PI Recon."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Preferred CTF shape; also allow common wrappers.
_WRAPPED = re.compile(
    r"^(?:flag|FLAG|ctf|CTF|bctf|BCTF|loot|LOOT)\{[A-Za-z0-9_+\-./:=@]{4,200}\}$"
)
_PRINTABLE = re.compile(r"^[\x20-\x7E]+$")
_PLACEHOLDER = re.compile(
    r"^(?:flag|FLAG|ctf|CTF|bctf|BCTF|loot|LOOT)\{"
    r"(?:test|flag|password|passwd|admin|xxx|TODO|todo|placeholder|example|sample|fake|null|none|"
    r"th1s_r0w_1s_a_d3c0y.*|decoy.*|keep_l00k1ng.*)"
    r".*\}$",
    re.I,
)
_DECOY_WORDS = re.compile(
    r"decoy|keep[_ ]?look|fake[_ ]?flag|not[_ ]?the[_ ]?flag|example|placeholder|TODO",
    re.I,
)

_HISTORY = "submit_history.json"
_DEFAULT_MAX_WRONG = 3


def normalize(raw: str) -> str:
    s = (raw or "").strip()
    if (s.startswith("`") and s.endswith("`")) or (s.startswith('"') and s.endswith('"')):
        s = s[1:-1].strip()
    if "\n" in s or "\r" in s:
        s = s.splitlines()[0].strip()
    return s


def validate_loot(flag: str) -> Tuple[bool, str]:
    s = normalize(flag)
    if not s:
        return False, "empty"
    if len(s) < 8:
        return False, "too_short"
    if len(s) > 240:
        return False, "too_long"
    if not _PRINTABLE.match(s):
        return False, "non_printable"
    if "\\x" in s.lower() or "\\u" in s.lower() or "\\0" in s:
        return False, "escape_noise"
    if any(ord(c) < 32 for c in s):
        return False, "control_char"
    if s.count("{") != s.count("}"):
        return False, "unbalanced_braces"
    if _PLACEHOLDER.match(s) or _DECOY_WORDS.search(s):
        return False, "placeholder_or_decoy"
    if _WRAPPED.match(s):
        return True, "ok"
    # bare long tokens that still look intentional
    if re.match(r"^(?:flag|ctf|bctf)[_-][A-Za-z0-9_+\-./:=@]{6,200}$", s, re.I):
        return True, "ok_bare"
    return False, "bad_shape"


def _hist_path(job_dir: Path) -> Path:
    return job_dir / _HISTORY


def load_history(job_dir: Path) -> Dict[str, Any]:
    p = _hist_path(job_dir)
    if not p.is_file():
        return {
            "ok": [],
            "wrong": [],
            "rejected_local": [],
            "attempts": [],
            "consecutive_wrong": 0,
        }
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("ok", [])
            data.setdefault("wrong", [])
            data.setdefault("rejected_local", [])
            data.setdefault("attempts", [])
            data.setdefault("consecutive_wrong", 0)
            return data
    except Exception:  # noqa: BLE001
        pass
    return {
        "ok": [],
        "wrong": [],
        "rejected_local": [],
        "attempts": [],
        "consecutive_wrong": 0,
    }


def save_history(job_dir: Path, hist: Dict[str, Any]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    _hist_path(job_dir).write_text(
        json.dumps(hist, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def max_wrong() -> int:
    import os

    raw = (os.environ.get("PI_RECON_SUBMIT_MAX_WRONG") or "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return _DEFAULT_MAX_WRONG


def filter_candidates(cands: List[str], job_dir: Path) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Return (accepted_for_network, local_rejects[(flag, reason)])."""
    hist = load_history(job_dir)
    ok_set = set(hist.get("ok") or [])
    wrong_set = set(hist.get("wrong") or [])
    out: List[str] = []
    rejects: List[Tuple[str, str]] = []
    seen: set = set()
    for raw in cands:
        s = normalize(raw)
        if not s or s in seen:
            continue
        seen.add(s)
        good, reason = validate_loot(s)
        if not good:
            rejects.append((s, reason))
            hist.setdefault("rejected_local", []).append({"flag": s, "reason": reason})
            continue
        if s in ok_set:
            continue  # already scored — skip re-POST
        if s in wrong_set:
            rejects.append((s, "already_wrong"))
            continue
        out.append(s)
    save_history(job_dir, hist)
    return out, rejects


def record_attempt(
    job_dir: Path,
    flag: str,
    *,
    ok: bool,
    wrong: bool = False,
    detail: str = "",
) -> Dict[str, Any]:
    hist = load_history(job_dir)
    s = normalize(flag)
    hist.setdefault("attempts", []).append(
        {"flag": s, "ok": ok, "wrong": wrong, "detail": detail[:300]}
    )
    if ok:
        if s not in hist["ok"]:
            hist["ok"].append(s)
        hist["consecutive_wrong"] = 0
    elif wrong:
        if s not in hist["wrong"]:
            hist["wrong"].append(s)
        hist["consecutive_wrong"] = int(hist.get("consecutive_wrong") or 0) + 1
    save_history(job_dir, hist)
    return hist


def circuit_open(job_dir: Path) -> bool:
    hist = load_history(job_dir)
    return int(hist.get("consecutive_wrong") or 0) >= max_wrong()
