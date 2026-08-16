"""Platform HTTP glue for challenge list/start/submit/close.

Standalone PI Recon platform adapter.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# Distinct product fingerprint (do not reuse other agents' UA)
UA = "pi-recon/1.4.0"


def _creds() -> Tuple[str, str]:
    base = (os.environ.get("BENCHMARK_BASE_URL") or "").strip().rstrip("/")
    token = (os.environ.get("BENCHMARK_TOKEN") or "").strip()
    if not base or not token:
        raise SystemExit("missing BENCHMARK_BASE_URL / BENCHMARK_TOKEN")
    return base, token


def _log(msg: str) -> None:
    print(msg, flush=True)


def api(
    method: str,
    path: str,
    *,
    body: Optional[dict] = None,
    timeout: int = 60,
    quiet: bool = False,
) -> Any:
    base, token = _creds()
    url = f"{base}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "BENCHMARK_TOKEN": token,
        "User-Agent": UA,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"

    if not quiet:
        body_preview = ""
        if body is not None:
            safe = {}
            for k, v in body.items():
                s = str(v)
                safe[k] = s if len(s) <= 120 else s[:120] + "…"
            body_preview = f" body={json.dumps(safe, ensure_ascii=False)}"
        _log(f"[wire] → {method} {path}{body_preview}")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(text) if text.strip() else None
            if not quiet:
                preview = text if len(text) <= 800 else text[:800] + "…"
                _log(f"[wire] ← {resp.status} {path} {preview}")
            return parsed
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        if not quiet:
            _log(f"[wire] ← HTTP {e.code} {method} {path}: {text[:800]}")
        raise RuntimeError(f"HTTP {e.code} {method} {path}: {text}") from e


def list_challenges() -> List[dict]:
    data = api("GET", "/openapi/v1/challenges")
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected challenges payload: {type(data)}")
    _log(f"[wire] catalog size={len(data)}")
    return data


def _addrs_if_up(code: str) -> List[str]:
    code_l = (code or "").strip().lower()
    try:
        for c in list_challenges():
            uc = str(c.get("unique_code") or "").strip()
            if uc.lower() != code_l:
                continue
            st = (c.get("container_status") or "").lower()
            addrs = list(c.get("container_addr") or [])
            if st in {"available", "pending", "running", "starting"} and addrs:
                return addrs
            if st in {"available", "pending", "running", "starting"} and not addrs:
                for _ in range(15):
                    time.sleep(2)
                    for c2 in list_challenges():
                        if str(c2.get("unique_code") or "").lower() != code_l:
                            continue
                        addrs2 = list(c2.get("container_addr") or [])
                        if addrs2:
                            return addrs2
                        break
            break
    except Exception as e:  # noqa: BLE001
        print(f"[warn] pre-start list {code}: {e}", file=sys.stderr, flush=True)
    return []


def start_challenge(code: str, *, max_wait_sec: int = 1800) -> List[str]:
    """Start challenge; if platform is at max concurrent instances (often 3), wait & retry."""
    existing = _addrs_if_up(code)
    if existing:
        _log(f"[{code}] reuse live instance {existing}")
        return existing

    t0 = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        try:
            data = api(
                "POST",
                f"/openapi/v1/challenges/start?unique_code={urllib.parse.quote(code, safe='')}",
                timeout=120,
            )
            break
        except Exception as e:  # noqa: BLE001
            existing = _addrs_if_up(code)
            if existing:
                _log(f"[{code}] start err but live: {e}; reuse {existing}")
                return existing
            err = str(e)
            congested = (
                "max active" in err.lower()
                or "resource_unavailable" in err.lower()
                or "HTTP 409" in err
                or "HTTP 503" in err
            )
            elapsed = time.monotonic() - t0
            if congested and elapsed < max_wait_sec:
                sleep_s = min(30, 3 + attempt)
                _log(
                    f"[{code}] platform concurrent full (attempt={attempt} "
                    f"elapsed={elapsed:.0f}s) — wait {sleep_s}s then retry start"
                )
                time.sleep(sleep_s)
                continue
            raise

    addrs = list((data or {}).get("container_addr") or [])
    for i in range(30):
        if addrs:
            _log(f"[{code}] instance ready addrs={addrs}")
            return addrs
        time.sleep(2)
        for c in list_challenges():
            if c.get("unique_code") == code:
                if c.get("container_status") == "available" and c.get("container_addr"):
                    addrs = list(c["container_addr"])
                    _log(f"[{code}] polled ready addrs={addrs} tick={i}")
                    return addrs
                break
    _log(f"[{code}] empty addrs after poll")
    return addrs


def submit_flag(code: str, flag: str) -> Dict[str, Any]:
    data = api(
        "POST",
        "/openapi/v1/challenges/submit",
        body={"unique_code": code, "flag": flag},
        timeout=60,
    )
    if not isinstance(data, dict):
        return {"raw": data}
    return data


def fetch_platform_hint(code: str) -> Optional[str]:
    """GET official hint (viewing applies score penalty on later correct submits)."""
    q = urllib.parse.quote(str(code), safe="")
    try:
        data = api("GET", f"/openapi/v1/challenges/hint?unique_code={q}", timeout=30)
    except Exception as e:  # noqa: BLE001
        _log(f"[{code}] hint fetch failed: {e}")
        return None
    if not isinstance(data, dict):
        return None
    hint = data.get("hint")
    if hint is None:
        return None
    text = str(hint).strip()
    return text or None


def close_challenge(code: str, *, retries: int = 4, timeout: int = 90) -> bool:
    last_err: Optional[BaseException] = None
    q = urllib.parse.quote(str(code), safe="")
    for i in range(max(1, retries)):
        try:
            api("POST", f"/openapi/v1/challenges/close?unique_code={q}", timeout=timeout)
            _log(f"[{code}] released (try {i + 1}/{retries})")
            return True
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[warn] release {code} try {i + 1}/{retries}: {e}", file=sys.stderr, flush=True)
            time.sleep(2 + i * 2)
            try:
                for c in list_challenges():
                    if str(c.get("unique_code") or "") == code:
                        st = (c.get("container_status") or "").lower()
                        if st in {"", "closed", "none", "stopped", "unavailable"}:
                            _log(f"[{code}] already down status={st!r}")
                            return True
                        break
            except Exception:  # noqa: BLE001
                pass
    print(f"[warn] release {code} failed: {last_err}", file=sys.stderr, flush=True)
    return False


def target_url(addrs: List[str]) -> str:
    if not addrs:
        return ""
    for a in addrs:
        s = str(a).strip()
        if not s:
            continue
        if s.startswith("http://") or s.startswith("https://"):
            return s
        if "://" not in s:
            return f"http://{s}"
    return str(addrs[0]).strip()
