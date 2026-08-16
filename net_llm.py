"""Pick LLM base URL for hosted sandboxes (TSec gateway vs public)."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse, urlunparse

TSEC_GW_SUFFIX = ".tsecbench.gw"


def default_models_path() -> Path:
    env_dir = (os.environ.get("PI_CODING_AGENT_DIR") or "").strip()
    if env_dir:
        return Path(env_dir).expanduser() / "models.json"
    # container image uses /root; local uses $HOME
    docker = Path("/root/.pi/agent/models.json")
    if docker.is_file() or Path("/.dockerenv").exists() or os.environ.get("PI_RECON_IN_DOCKER"):
        return docker
    return Path.home() / ".pi" / "agent" / "models.json"


DEFAULT_MODELS = default_models_path()  # evaluated at import; prefer default_models_path() at runtime

@dataclass
class Route:
    mode: str
    base_url: str
    reason: str
    probe_ok: Optional[bool] = None


def _log(msg: str) -> None:
    print(msg, flush=True)


def inject_tsec_gw(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return raw
    if TSEC_GW_SUFFIX in raw:
        return raw.replace("https://", "http://", 1) if raw.startswith("https://") else raw
    p = urlparse(raw)
    host = p.hostname or ""
    if not host:
        return raw
    if not host.endswith(TSEC_GW_SUFFIX):
        host = f"{host}{TSEC_GW_SUFFIX}"
    netloc = f"{host}:{p.port}" if p.port and p.port not in (80, 443) else host
    return urlunparse(("http", netloc, p.path or "", p.params, p.query, p.fragment))


def probe_tcp(url: str, timeout: float = 2.0) -> bool:
    p = urlparse(url if "://" in url else f"http://{url}")
    host = p.hostname or ""
    if not host:
        return False
    if p.port:
        port = p.port
    elif (p.scheme or "http") == "https":
        port = 443
    else:
        port = 80
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def read_models_base(provider: str = "deepseek", models_path: Optional[Path] = None) -> str:
    path = models_path or Path(os.environ.get("PI_MODELS_JSON") or str(default_models_path()))
    if not path.is_file():
        return (os.environ.get("DEEPSEEK_BASE_URL") or os.environ.get("LLM_BASE_URL") or "").strip()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        bu = ((data.get("providers") or {}).get(provider) or {}).get("baseUrl") or ""
        return str(bu).strip().rstrip("/")
    except (OSError, json.JSONDecodeError, TypeError):
        return ""


def write_models_base(base_url: str, provider: str = "deepseek", models_path: Optional[Path] = None) -> None:
    path = models_path or Path(os.environ.get("PI_MODELS_JSON") or str(default_models_path()))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _log(f"[llm] warn: cannot mkdir {path.parent}: {e}")
        return
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"providers": {}}
    provs = data.setdefault("providers", {})
    if not isinstance(provs.get(provider), dict):
        provs[provider] = {
            "baseUrl": base_url,
            "api": "openai-completions",
            "models": [],
        }
    else:
        provs[provider]["baseUrl"] = base_url
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prefer_mode() -> str:
    route = (os.environ.get("PI_RECON_LLM_ROUTE") or os.environ.get("LLM_ROUTE") or "").strip().lower()
    if route in {"gateway", "gw", "tsec", "hosted"}:
        return "force_gateway"
    if route in {"public", "local", "direct", "baidu"}:
        return "force_public"
    if (os.environ.get("TSEC_HOSTED") or "").strip() in {"1", "true", "yes"}:
        return "force_gateway"
    return "auto"


def resolve_route(provider: str = "deepseek") -> Route:
    public = (
        (os.environ.get("PI_RECON_LLM_BASE") or os.environ.get("DEEPSEEK_BASE_URL") or os.environ.get("LLM_BASE_URL") or "").strip().rstrip("/")
        or read_models_base(provider)
        or "https://api.deepseek.com/v1"
    )
    # Baidu AWD host must stay public (no tsec rewrite)
    if "agent-awd.baidu.com" in public:
        return Route("public", public, "baidu AWD gateway (no tsec rewrite)")

    if TSEC_GW_SUFFIX in public:
        gw = inject_tsec_gw(public)
        return Route("gateway", gw, "base already gateway")

    gw = inject_tsec_gw(public)
    pref = prefer_mode()
    if pref == "force_public":
        return Route("public", public, "LLM_ROUTE=public")

    ok = probe_tcp(gw)
    if ok:
        return Route(
            "gateway",
            gw,
            f"tsec gateway reachable ({gw})",
            probe_ok=True,
        )
    if pref == "force_gateway":
        # still try gateway URL even if TCP probe failed (some sandboxes block probe weirdly)
        return Route(
            "gateway",
            gw,
            f"hosted force gateway but probe failed; still using {gw}",
            probe_ok=False,
        )
    # auto degrade
    pub_ok = probe_tcp(public)
    return Route(
        "public",
        public,
        f"gateway unreachable; public probe={'ok' if pub_ok else 'fail'} → {public}",
        probe_ok=False,
    )


def http_ping_chat(base_url: str, api_key: str, model: str, timeout: float = 20.0) -> Tuple[bool, str]:
    """Minimal OpenAI-compatible ping; returns (ok, detail)."""
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
            "stream": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return True, f"HTTP {resp.status} body={text[:200]}"
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        # 4xx means network path works
        if 400 <= e.code < 500:
            return True, f"HTTP {e.code} (reachable) body={text[:200]}"
        return False, f"HTTP {e.code} body={text[:300]}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def apply_route_and_diagnose(
    *,
    provider: str = "deepseek",
    model: str = "deepseek-v4-flash",
    api_key: str = "",
) -> Route:
    route = resolve_route(provider)
    write_models_base(route.base_url, provider=provider)
    os.environ["DEEPSEEK_BASE_URL"] = route.base_url
    os.environ["LLM_BASE_URL"] = route.base_url
    _log(f"[llm] route={route.mode} base={route.base_url}")
    _log(f"[llm] reason={route.reason} tcp_probe={route.probe_ok}")
    if api_key:
        ok, detail = http_ping_chat(route.base_url, api_key, model)
        _log(f"[llm] chat_ping ok={ok} detail={detail}")
        if not ok:
            # one more try: if public baidu-style failed and we weren't on gateway, try gateway of deepseek public
            if "agent-awd.baidu.com" in route.base_url:
                alt = "https://api.deepseek.com/v1"
                gw = inject_tsec_gw(alt)
                if probe_tcp(gw) or prefer_mode() in {"force_gateway", "auto"}:
                    _log(f"[llm] baidu base failed; trying TSec deepseek gateway {gw}")
                    write_models_base(gw, provider=provider)
                    os.environ["DEEPSEEK_BASE_URL"] = gw
                    ok2, detail2 = http_ping_chat(gw, api_key, model)
                    _log(f"[llm] chat_ping(alt) ok={ok2} detail={detail2}")
                    if ok2:
                        return Route("gateway", gw, "fallback from baidu to tsec deepseek", True)
            _log("[llm] WARNING: model API not reachable — pi sessions will print Connection error")
    else:
        _log("[llm] WARNING: no api key for chat_ping")
    return route


if __name__ == "__main__":
    key = (
        os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("API_KEY")
        or ""
    ).strip()
    apply_route_and_diagnose(
        provider=os.environ.get("PROVIDER") or "deepseek",
        model=os.environ.get("MODEL") or "deepseek-v4-flash",
        api_key=key,
    )
