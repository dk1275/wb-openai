#!/usr/bin/env python3
"""
wb-openai converter — 把新版 WorkBuddy / CodeBuddy 桌面端订阅暴露成标准 OpenAI 兼容 API。

上游：https://copilot.tencent.com/v2/chat/completions（原生 OpenAI chat 协议，含 tools/tool_calls/SSE）
本地：FastAPI，/v1/chat/completions、/v1/models、/health

与原版 codebuddy2openai 的差异（本版解决新版客户端加密凭据跑不通的问题）：
  1. 凭据解密：新版桌面端把 auth 文件里的 accessToken/refreshToken 等字段写成了
     {"$wbEncrypted":1,"envelope":"..."} 加密信封。本版启动时调 wb_decode 从本机
     Electron 二进制取 atRestSecretKey 并 AES-256-GCM 解密，只在内存里持有明文。
  2. 客户端指纹：User-Agent 用官方三段式 WorkBuddy/<v> WorkBuddy/<v> CLI/<v>，
     版本号从安装包的 cli/product.json 实读，不写死。
  3. 设备风控头 X-Device-Token：可选，--device-token 启用（需要桌面端 TuringShield
     SDK 可用）。默认关闭，关闭时后端按无设备头处理。
  4. token 刷新改为仅内存生效，不回写加密 .info 文件（避免把明文 token 写进客户端
     加密文件把桌面端搞坏）。

只读客户端文件，不修改任何登录态。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

from wb_decode import normalize_credential, DecodeError

try:
    from desensitize import desensitize_body
except ImportError:  # 模块缺失时降级为不脱敏
    def desensitize_body(body, roles=("system",)):
        return body

BACKEND = "https://copilot.tencent.com"
DEFAULT_DOMAIN = "www.codebuddy.cn"
TOOL_NAME = "wb-openai/2.1"

# ---------------------------------------------------------------------------
# 客户端版本探测（读安装包，不写死）
# ---------------------------------------------------------------------------

def _install_meta() -> dict:
    """从本机安装包读 productName / version / channelId。读不到返回空。"""
    bases = []
    if sys.platform == "darwin":
        for app in ("/Applications/WorkBuddy.app", "/Applications/CodeBuddy.app",
                    str(Path.home() / "Applications" / "WorkBuddy.app"),
                    str(Path.home() / "Applications" / "CodeBuddy.app")):
            bases.append(Path(app) / "Contents" / "Resources")
    else:
        for d in ("C:\\WorkBuddy", "C:\\workbuddy"):
            bases.append(Path(d) / "resources")
    out = {}
    for b in bases:
        p = b / "app.asar.unpacked" / "cli" / "product.json"
        if p.is_file():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if d.get("applicationName"):
                    out["productName"] = d["applicationName"]
                if d.get("genieVersion"):
                    out["version"] = d["genieVersion"]
                turing = (d.get("config") or {}).get("turingSdk") or {}
                if turing.get("channelId"):
                    out["channelId"] = turing["channelId"]
            except Exception:
                pass
            break
    return out


def _sdk_dir() -> Path | None:
    """定位桌面端自带 TuringShield SDK 目录。"""
    env = os.environ.get("WORKBUDDY_TURING_SDK_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    bases = []
    if sys.platform == "darwin":
        for app in ("/Applications/WorkBuddy.app", "/Applications/CodeBuddy.app"):
            bases.append(Path(app) / "Contents" / "Resources" / "app.asar.unpacked" / "native" / "turing-sdk")
    else:
        for d in ("C:\\WorkBuddy", "C:\\workbuddy"):
            bases.append(Path(d) / "resources" / "app.asar.unpacked" / "native" / "turing-sdk")
    for b in bases:
        if (b / "index.cjs").is_file():
            return b
    return None


def _node_bin() -> str | None:
    for cand in (os.environ.get("WB_NODE_BIN"), "node", "node.exe"):
        if cand:
            from shutil import which
            p = which(cand)
            if p:
                return p
    return None


def client_user_agent() -> str:
    meta = _install_meta()
    brand = meta.get("productName", "WorkBuddy")
    v = meta.get("version", "5.7.7")
    return f"{brand}/{v} {brand}/{v} CLI/{v}"


# ---------------------------------------------------------------------------
# 设备风控 token（可选）
# ---------------------------------------------------------------------------

_DT_LOCK = threading.Lock()
_DT_CACHE: dict = {"token": None, "ts": 0.0}
_DT_TTL = 540.0


def get_device_token(force: bool = False) -> str | None:
    """取 X-Device-Token；取不到返回 None（调用方优雅降级）。"""
    now = time.time()
    with _DT_LOCK:
        if not force and _DT_CACHE["token"] and now - _DT_CACHE["ts"] < _DT_TTL:
            return _DT_CACHE["token"]

    sdk = _sdk_dir()
    node = _node_bin()
    if sdk is None or node is None:
        return None

    meta = _install_meta()
    helper = Path(__file__).resolve().parent / "turing_helper.js"
    if not helper.is_file():
        return None

    env = dict(os.environ)
    env["WORKBUDDY_TURING_SDK_DIR"] = str(sdk)
    env.setdefault("WORKBUDDY_TURING_CHANNEL_ID", str(meta.get("channelId", 109144)))
    env.setdefault("WORKBUDDY_TURING_PRODUCT_NAME", meta.get("productName", "WorkBuddy"))
    env.setdefault("WORKBUDDY_TURING_VERSION", meta.get("version", "5.7.7"))
    import subprocess
    try:
        r = subprocess.run([node, str(helper)], capture_output=True, text=True,
                           timeout=25, env=env)
    except Exception:
        return None
    if r.returncode != 0:
        sys.stderr.write(f"[wb-openai] 设备令牌获取失败：{r.stderr.strip()[:200]}\n")
        return None
    try:
        token = (json.loads(r.stdout).get("token") or "").strip()
    except Exception:
        return None
    if not token:
        return None
    with _DT_LOCK:
        _DT_CACHE["token"] = token
        _DT_CACHE["ts"] = time.time()
    return token


# ---------------------------------------------------------------------------
# 平台相关：定位 auth 目录
# ---------------------------------------------------------------------------

def auth_dirs() -> list[Path]:
    home = Path.home()
    plat = sys.platform
    if plat == "darwin":
        return [home / "Library" / "Application Support" / "CodeBuddyExtension" / "Data" / "Public" / "auth"]
    if plat == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return [local / "CodeBuddyExtension" / "Data" / "Public" / "auth"]
    xdg = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    return [xdg / "CodeBuddyExtension" / "Data" / "Public" / "auth"]


def find_auth_file() -> Path | None:
    for d in auth_dirs():
        if d.is_dir():
            for f in sorted(d.glob("*.info")):
                return f
    return None


# ---------------------------------------------------------------------------
# Auth 凭据管理（解密读 + 内存态）
# ---------------------------------------------------------------------------

class CredentialManager:
    """读 auth 文件；加密字段解密后只进内存。token 过期时刷新，仅更新内存。"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._cached: dict | None = None
        self._mtime: float = 0.0
        self._decoded: dict | None = None   # 解密后的 session（内存态）
        self._decode_error: str | None = None

    def _read_raw(self) -> dict:
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_if_stale(self):
        """客户端可能自己刷新过文件；mtime 变了就重新读+重新解密。"""
        try:
            mt = self.path.stat().st_mtime
        except OSError:
            return
        if self._cached is None or mt != self._mtime:
            raw = self._read_raw()
            self._cached = raw
            self._mtime = mt
            try:
                plain = normalize_credential(json.dumps(raw, ensure_ascii=False))
                self._decoded = json.loads(plain)
                self._decode_error = None
            except DecodeError as e:
                self._decoded = None
                self._decode_error = str(e)
            except Exception as e:
                self._decoded = None
                self._decode_error = f"解密失败：{e}"

    def _session(self) -> dict:
        self._load_if_stale()
        if self._decoded is not None:
            return self._decoded
        if self._decode_error:
            raise RuntimeError(self._decode_error)
        # 老格式（明文）直接可用
        if self._cached is None:
            raise RuntimeError(f"无法读取 auth 文件：{self.path}")
        return self._cached

    def _is_expired(self) -> bool:
        s = self._session()
        expires_at = (s.get("auth") or {}).get("expiresAt") or 0
        return time.time() * 1000 >= (expires_at - 60_000)

    def _refresh(self):
        """调后端刷新 token。只更新内存，不回写加密文件。"""
        s = self._session()
        auth = s.get("auth") or {}
        headers = self._build_headers_from(auth, s.get("account") or {})
        headers["X-Refresh-Token"] = auth.get("refreshToken", "")
        headers["X-Auth-Refresh-Source"] = "plugin"
        url = f"{BACKEND}/v2/plugin/auth/token/refresh"
        try:
            with httpx.Client(timeout=15) as c:
                r = c.post(url, headers=headers, json={})
            data = r.json()
        except Exception as e:
            raise RuntimeError(f"刷新 token 网络失败：{e}")
        if data.get("code") != 0 or not data.get("data"):
            raise RuntimeError(f"刷新 token 失败：{data.get('msg', data)}")
        new_auth = data["data"]
        new_auth["domain"] = new_auth.get("domain") or auth.get("domain")
        new_auth["lastRefreshTime"] = int(time.time() * 1000)
        if not new_auth.get("expiresAt") and new_auth.get("expiresIn"):
            new_auth["expiresAt"] = int(time.time() * 1000) + new_auth["expiresIn"] * 1000
        if not new_auth.get("refreshExpiresAt") and new_auth.get("refreshExpiresIn"):
            new_auth["refreshExpiresAt"] = int(time.time() * 1000) + new_auth["refreshExpiresIn"] * 1000
        # 仅内存生效 —— 不回写 .info，避免污染客户端的加密文件
        s["auth"] = new_auth
        sys.stderr.write("[wb-openai] token 已刷新（仅内存，未写回客户端文件）\n")

    def _build_headers_from(self, auth: dict, account: dict) -> dict:
        domain = auth.get("domain") or DEFAULT_DOMAIN
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {auth.get('accessToken','')}",
            "X-User-Id": account.get("uid", ""),
            "X-Enterprise-Id": account.get("enterpriseId", ""),
            "X-Tenant-Id": account.get("enterpriseId", ""),
            "X-Domain": domain,
            "User-Agent": client_user_agent(),
        }

    def get_headers(self, with_device: bool = False) -> dict:
        with self._lock:
            if self._is_expired():
                self._refresh()
            s = self._session()
            h = self._build_headers_from(s.get("auth") or {}, s.get("account") or {})
        if with_device:
            tok = get_device_token()
            if tok:
                h["X-Device-Token"] = tok
        return h

    def summary(self) -> dict:
        s = self._session()
        auth = s.get("auth") or {}
        acct = s.get("account") or {}
        exp = auth.get("expiresAt", 0)
        return {
            "uid": acct.get("uid"),
            "nickname": acct.get("nickname"),
            "enterpriseName": acct.get("enterpriseName"),
            "token_type": type(auth.get("accessToken")).__name__,
            "token_len": len(auth.get("accessToken") or ""),
            "token_expires_at": exp,
            "token_expired": self._is_expired(),
        }


# ---------------------------------------------------------------------------
# 模型列表
# ---------------------------------------------------------------------------

DEFAULT_MODELS = [
    "glm-5.2", "glm-5.1", "glm-5v-turbo",
    "kimi-k2.7", "kimi-k2.6", "kimi-k2.5",
    "deepseek-v4-pro", "deepseek-v4-flash",
    "minimax-m3-pay", "hy3-preview-agent", "auto",
]

PASSTHROUGH_BODY_KEYS = {
    "model", "messages", "tools", "tool_choice", "temperature",
    "max_tokens", "max_completion_tokens", "top_p", "stream",
    "stream_options", "stop", "presence_penalty", "frequency_penalty",
    "n", "response_format", "seed", "user", "reasoning_effort",
    "verbosity", "reasoning_summary",
}

# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------

app = FastAPI(title="wb-openai", version="2.1")
CONFIG: dict = {"api_key": "", "cred": None, "log_path": None,
                "desensitize": False, "device_token": False}

_LOG_LOCK = threading.Lock()


def _log(msg: str):
    path = CONFIG.get("log_path")
    if not path:
        return
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        with _LOG_LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except OSError:
        pass


def _truncate(s: str, n: int = 80) -> str:
    s = str(s).replace("\n", " ").strip()
    return s[:n] + ("…" if len(s) > n else "")


def _check_auth(authorization: Optional[str], x_api_key: Optional[str]):
    key = CONFIG["api_key"]
    if not key:
        return
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    if not token and x_api_key:
        token = x_api_key
    if token != key:
        raise HTTPException(status_code=401, detail={"error": {"message": "invalid api key", "type": "auth_error"}})


def _cred() -> CredentialManager:
    if CONFIG["cred"] is None:
        raise HTTPException(status_code=503, detail={"error": {"message": "未找到登录凭据，请先在桌面端登录 CodeBuddy/WorkBuddy", "type": "auth_error"}})
    return CONFIG["cred"]


@app.get("/health")
def health():
    cred = CONFIG["cred"]
    info: dict = {"status": "ok", "platform": sys.platform, "python": sys.version.split()[0],
                  "mode": "decrypt-proxy", "user_agent": client_user_agent(),
                  "auth_file": str(find_auth_file() or "(未找到)"),
                  "device_token": bool(CONFIG.get("device_token"))}
    if cred is not None:
        try:
            info["credential"] = cred.summary()
        except Exception as e:
            info["credential_error"] = str(e)
    return info


@app.get("/v1/models")
def list_models(authorization: Optional[str] = Header(default=None),
                x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key")):
    _check_auth(authorization, x_api_key)
    data = [{"id": m, "object": "model", "created": 1700000000, "owned_by": "codebuddy"}
            for m in DEFAULT_MODELS]
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request,
                           authorization: Optional[str] = Header(default=None),
                           x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key")):
    _check_auth(authorization, x_api_key)
    cred = _cred()

    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": {"message": f"bad json: {e}", "type": "invalid_request_error"}})

    messages = payload.get("messages") or []
    if not messages:
        raise HTTPException(status_code=400, detail={"error": {"message": "messages is required", "type": "invalid_request_error"}})

    client_wants_stream = bool(payload.get("stream"))
    body = {k: payload[k] for k in PASSTHROUGH_BODY_KEYS if k in payload}
    body.setdefault("model", "auto")
    body["stream"] = True
    if "stream_options" not in body:
        body["stream_options"] = {"include_usage": True}

    # 可选：脱敏。缓解客户端合规模板（system 里的 DoS/exploit 等英文术语）被后端误拦。
    if CONFIG.get("desensitize"):
        body = desensitize_body(body, roles=("system",))

    model_name = payload.get("model", "auto")
    tool_names = [t.get("function", {}).get("name") for t in (payload.get("tools") or [])
                  if isinstance(t, dict)]
    last_user = _last_user_text(messages)
    rid = os.urandom(4).hex()
    _log(f"[{rid}] ▶ REQUEST {model_name} | stream={client_wants_stream} | msgs={len(messages)}"
         + (f" | tools={tool_names}" if tool_names else "")
         + (f" | last_user={_truncate(last_user, 60)!r}" if last_user else ""))

    try:
        headers = cred.get_headers(with_device=bool(CONFIG.get("device_token")))
    except Exception as e:
        _log(f"[{rid}] ✗ 凭据错误 | {e}")
        raise HTTPException(status_code=503, detail={"error": {"message": str(e), "type": "auth_error"}})
    url = f"{BACKEND}/v2/chat/completions"
    t0 = time.time()

    if client_wants_stream:
        return StreamingResponse(
            _stream_upstream(url, headers, body, model_name, t0, rid),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        async with httpx.AsyncClient(timeout=300) as c:
            async with c.stream("POST", url, headers=headers, json=body) as r:
                if r.status_code != 200:
                    raw = await r.aread()
                    _log(f"[{rid}] ✗ HTTP {r.status_code} | {model_name} | {_truncate(raw.decode('utf-8','replace'),200)}")
                    raise HTTPException(status_code=r.status_code, detail=_safe_err_raw(raw, r.status_code))
                collected = await _collect_stream(r)
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        _log(f"[{rid}] ✗ 网络错误 | {model_name} | {e}")
        raise HTTPException(status_code=502, detail={"error": {"message": f"upstream error: {e}", "type": "upstream_error"}})
    _log_finish(model_name, t0, collected, rid)
    return JSONResponse(content=collected)


def _last_user_text(messages: list) -> str:
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        content = m.get("content", "")
        if isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    return str(blk.get("text", ""))
            return ""
        return str(content)
    return ""


def _log_finish(model_name: str, t0: float, result: dict, rid: str = ""):
    elapsed = time.time() - t0
    prefix = f"[{rid}] " if rid else ""
    choice = (result.get("choices") or [{}])[0]
    finish = choice.get("finish_reason")
    msg = choice.get("message") or {}
    tcs = msg.get("tool_calls") or []
    usage = result.get("usage") or {}
    tag = " ⚠️内容审核拦截" if finish == "content-filter" else ""
    tc_names = [t.get("function", {}).get("name") for t in tcs]
    _log(f"{prefix}◀ RESPONSE {model_name} | {elapsed:.1f}s | finish={finish}{tag}"
         + (f" | tool_calls={tc_names}" if tc_names else "")
         + f" | tokens={usage.get('total_tokens', '?')}")


async def _collect_stream(response: httpx.Response) -> dict:
    content_parts: list[str] = []
    tool_calls: dict[int, dict] = {}
    model: str | None = None
    finish_reason: str | None = None
    usage: dict | None = None

    async for line in response.aiter_lines():
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        model = chunk.get("model") or model
        if chunk.get("usage"):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content_parts.append(delta["content"])
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tool_calls.setdefault(idx, {"id": None, "name": None, "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]

    tcs = None
    if tool_calls:
        tcs = [
            {"id": v["id"], "type": "function",
             "function": {"name": v["name"], "arguments": v["arguments"]}}
            for _, v in sorted(tool_calls.items())
        ]
        finish_reason = finish_reason or "tool_calls"

    message = {"role": "assistant", "content": "".join(content_parts) or None}
    if tcs:
        message["tool_calls"] = tcs
    return {
        "id": "chatcmpl-" + os.urandom(12).hex(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model or "unknown",
        "choices": [{"index": 0, "message": message,
                     "finish_reason": finish_reason or "stop"}],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _safe_err_raw(raw: bytes, status: int) -> dict:
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {"error": {"message": raw.decode("utf-8", "replace")[:500], "type": "upstream_error", "code": status}}


async def _stream_upstream(url: str, headers: dict, body: dict,
                           model_name: str = "?", t0: float = 0.0, rid: str = ""):
    prefix = f"[{rid}] " if rid else ""
    try:
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("POST", url, headers=headers, json=body) as r:
                if r.status_code != 200:
                    err = await r.aread()
                    _log(f"{prefix}✗ HTTP {r.status_code} | {model_name} | {_truncate(err.decode('utf-8','replace'),200)}")
                    yield _err_event(err, r.status_code)
                    return
                async for chunk in r.aiter_bytes():
                    if chunk:
                        yield chunk
    except httpx.HTTPError as e:
        _log(f"{prefix}✗ 网络错误 | {model_name} | {e}")
        yield _err_event(str(e).encode(), 502)
    elapsed = time.time() - t0 if t0 else 0
    _log(f"{prefix}◀ RESPONSE {model_name} | {elapsed:.1f}s | stream done")


def _err_event(msg: bytes, status: int) -> bytes:
    chunk = {"error": {"message": msg.decode("utf-8", "replace")[:500],
                       "type": "upstream_error", "code": status}}
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def preflight(cred: CredentialManager | None) -> bool:
    sys.stderr.write("==== 预检 ====\n")
    sys.stderr.write(f"平台      : {sys.platform}\n")
    sys.stderr.write(f"Python    : {sys.version.split()[0]}\n")
    sys.stderr.write(f"后端      : {BACKEND}\n")
    sys.stderr.write(f"UA        : {client_user_agent()}\n")
    sys.stderr.write(f"登录文件  : {find_auth_file() or '(未找到)'}\n")
    ok = True
    if cred is None:
        sys.stderr.write("\n[警告] 未找到登录文件。请在桌面端完成登录。\n")
        return False
    try:
        info = cred.summary()
        sys.stderr.write(f"账号      : {info.get('nickname')}\n")
        sys.stderr.write(f"uid       : {info.get('uid')}\n")
        sys.stderr.write(f"token     : {info.get('token_type')} len={info.get('token_len')}\n")
        sys.stderr.write(f"token过期 : {'是(将自动刷新)' if info['token_expired'] else '否'}\n")
    except Exception as e:
        sys.stderr.write(f"[警告] 读取凭据失败：{e}\n")
        ok = False
    sys.stderr.write("================\n")
    return ok


def main():
    ap = argparse.ArgumentParser(description="WorkBuddy/CodeBuddy -> OpenAI 兼容转换器（解密直连）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--api-key", default=os.environ.get("CODEBUDDY2OPENAI_KEY", ""),
                    help="可选：要求客户端携带的 API key（默认不校验）")
    ap.add_argument("--log", default=None, metavar="PATH", help="开启日志并写到该文件")
    ap.add_argument("--desensitize", action="store_true", help="启用脱敏模块")
    ap.add_argument("--device-token", action="store_true",
                    help="注入 X-Device-Token 设备风控头（需要桌面端 TuringShield SDK 可用）")
    ap.add_argument("--skip-check", action="store_true", help="跳过启动预检")
    args = ap.parse_args()

    CONFIG["api_key"] = args.api_key
    CONFIG["desensitize"] = args.desensitize
    CONFIG["device_token"] = args.device_token
    CONFIG["log_path"] = args.log if args.log else os.environ.get("CODEBUDDY2OPENAI_LOG")
    af = find_auth_file()
    CONFIG["cred"] = CredentialManager(af) if af else None

    if not args.skip_check:
        preflight(CONFIG["cred"])

    if args.device_token:
        tok = get_device_token(force=True)
        sys.stderr.write(f"   设备令牌  : {'已获取' if tok else '获取失败（将不带该头发请求）'}\n")

    sys.stderr.write(f"\n✅ 监听 http://{args.host}:{args.port}\n")
    sys.stderr.write("   GET  /v1/models\n")
    sys.stderr.write("   POST /v1/chat/completions\n")
    sys.stderr.write("   GET  /health\n")
    if args.api_key:
        sys.stderr.write("   鉴权已启用\n")
    if CONFIG["log_path"]:
        sys.stderr.write(f"   日志      : {CONFIG['log_path']}\n")
    if args.device_token:
        sys.stderr.write("   设备风控头 : 已启用\n")
    sys.stderr.write("按 Ctrl+C 退出。\n\n")

    _log("==== wb-openai converter 启动 ====")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
