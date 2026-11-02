#!/usr/bin/env python3
"""
wb_panel.py — wb-openai 的 Web 控制面板。

面板本身常驻（默认 127.0.0.1:8788），负责拉起/关停转换器子进程（默认 127.0.0.1:8787）。
浏览器打开后，页面上一个按钮就能开启或关闭服务，不需要碰命令行。

用法：
    python3 wb_panel.py                # 默认 8788，启动后自动打开浏览器
    python3 wb_panel.py --no-browser   # 不自动开浏览器
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from converter import CredentialManager, find_auth_file

HERE = Path(__file__).resolve().parent
CONVERTER = HERE / "converter.py"
PANEL_PORT = 8788
CONVERTER_PORT = 8787

_state_lock = threading.Lock()
_state: dict = {
    "proc": None,          # subprocess.Popen | None
    "started_at": 0.0,
    "log_path": None,
    "port": CONVERTER_PORT,
    "extra_args": [],
    "last_error": "",
}

app = FastAPI(title="wb-openai panel")

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>wb-openai 控制面板</title>
<style>
  :root{
    --bg:#f5f6f8; --card:#fff; --line:#e3e6ea; --txt:#1f2329; --sub:#6b7280;
    --ok:#16a34a; --off:#9ca3af; --err:#dc2626; --accent:#2563eb;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
       background:var(--bg);color:var(--txt);padding:28px 20px 40px}
  .wrap{max-width:760px;margin:0 auto}
  h1{font-size:20px;margin:0 0 4px}
  .sub{color:var(--sub);font-size:13px;margin-bottom:20px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:14px}
  .row{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  .dot{width:11px;height:11px;border-radius:50%;background:var(--off);flex:none}
  .dot.on{background:var(--ok);box-shadow:0 0 0 4px rgba(22,163,74,.15)}
  .dot.err{background:var(--err);box-shadow:0 0 0 4px rgba(220,38,38,.15)}
  .status{font-size:15px;font-weight:600}
  .status small{font-weight:400;color:var(--sub);margin-left:8px;font-size:12px}
  button{border:0;border-radius:9px;padding:10px 22px;font-size:14px;font-weight:600;cursor:pointer;
         font-family:inherit;transition:.15s}
  .btn-start{background:var(--ok);color:#fff}
  .btn-start:hover{background:#15803d}
  .btn-stop{background:#fff;color:var(--err);border:1px solid var(--err)}
  .btn-stop:hover{background:#fef2f2}
  button:disabled{opacity:.45;cursor:not-allowed}
  .kv{display:grid;grid-template-columns:110px 1fr;gap:7px 14px;font-size:13px;margin-top:4px}
  .kv dt{color:var(--sub)}
  .kv dd{margin:0;word-break:break-all;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  .mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
  .copy{border:1px solid var(--line);background:#fafafa;border-radius:6px;padding:2px 9px;font-size:12px;
        cursor:pointer;color:var(--sub);margin-left:8px}
  .copy:hover{border-color:var(--accent);color:var(--accent)}
  #log{height:200px;overflow:auto;background:#0f172a;color:#cbd5e1;border-radius:8px;padding:12px 14px;
       font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;line-height:1.65;white-space:pre-wrap}
  h2{font-size:13px;color:var(--sub);margin:0 0 12px;font-weight:600;letter-spacing:.3px}
  .tip{font-size:12px;color:var(--sub);margin-top:10px;line-height:1.7}
  code{background:#f1f3f5;border-radius:4px;padding:1px 6px;font-size:12px}
</style>
</head>
<body>
<div class="wrap">
  <h1>wb-openai 控制面板</h1>
  <div class="sub">把 WorkBuddy / CodeBuddy 桌面端订阅转成标准 OpenAI 兼容 API</div>

  <div class="card">
    <div class="row">
      <span class="dot" id="dot"></span>
      <span class="status" id="status">检测中…</span>
      <span style="flex:1"></span>
      <button class="btn-start" id="btnStart">开启服务</button>
      <button class="btn-stop" id="btnStop" style="display:none">关闭服务</button>
    </div>
    <div class="tip" id="hint"></div>
  </div>

  <div class="card">
    <h2>账号与凭据</h2>
    <dl class="kv" id="acct"><dt>状态</dt><dd>读取中…</dd></dl>
  </div>

  <div class="card">
    <h2>客户端接入</h2>
    <dl class="kv">
      <dt>API Base</dt><dd><span id="base"></span><button class="copy" onclick="cp('base')">复制</button></dd>
      <dt>API Key</dt><dd>留空（未启用鉴权）</dd>
      <dt>模型名</dt><dd>glm-5.2 · kimi-k2.7 · deepseek-v4-pro · deepseek-v4-flash · auto</dd>
    </dl>
    <div class="tip">在 Cherry Studio / NextChat / LobeChat 等任意 OpenAI 兼容客户端里，
      选「自定义模型 / OpenAI 兼容」，填上面的 API Base 即可。</div>
  </div>

  <div class="card">
    <h2>运行日志</h2>
    <div id="log">（开启服务后这里会显示转换器输出）</div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
let running = false;

function cp(id){
  const t = $(id).textContent;
  navigator.clipboard.writeText(t).then(()=>{
    const b = event.target, o = b.textContent;
    b.textContent = '已复制'; setTimeout(()=>b.textContent = o, 1200);
  });
}

async function refresh(){
  try{
    const r = await fetch('/api/status'); const d = await r.json();
    running = d.running;
    $('dot').className = 'dot' + (d.running ? ' on' : (d.last_error ? ' err' : ''));
    $('status').innerHTML = d.running
      ? '运行中' + (d.uptime ? ' <small>已运行 ' + d.uptime + '</small>' : '')
      : '已停止' + (d.last_error ? ' <small style="color:var(--err)">' + esc(d.last_error) + '</small>' : '');
    $('btnStart').style.display = d.running ? 'none' : '';
    $('btnStop').style.display  = d.running ? '' : 'none';
    $('base').textContent = 'http://127.0.0.1:' + d.port + '/v1';
    if(d.log) { $('log').textContent = d.log; }
    renderAcct(d);
    $('hint').textContent = d.running
      ? '服务已在本地运行，直接用上方的 API Base 调用即可。关闭后所有客户端请求会立即失败。'
      : '点击「开启服务」启动。首次启动会解密桌面端登录凭据，需要桌面端保持登录状态。';
  }catch(e){ $('status').textContent = '面板通信失败'; }
}

function renderAcct(d){
  const c = d.credential, e = d.credential_error;
  if(e){
    $('acct').innerHTML = '<dt>错误</dt><dd style="color:var(--err)">' + esc(e) + '</dd>';
    return;
  }
  if(!c){
    $('acct').innerHTML = '<dt>状态</dt><dd style="color:var(--sub)">读取中…（首次解密需要几秒）</dd>';
    return;
  }
  const exp = c.token_expires_at || 0;
  const days = Math.round((exp/1000 - Date.now()/1000)/86400);
  const from = d.credential_source === 'panel' ? '本地读取' : '服务实时';
  $('acct').innerHTML =
      '<dt>账号</dt><dd>' + esc(c.nickname || '-') + '</dd>'
    + '<dt>uid</dt><dd>' + esc(c.uid || '-') + '</dd>'
    + '<dt>token</dt><dd>' + esc(c.token_type || '-') + '（' + (c.token_len||0) + ' 字符）</dd>'
    + '<dt>有效期</dt><dd>' + esc(c.token_expired
        ? '已过期，开启服务时会自动刷新'
        : (days > 0 ? '约 ' + days + ' 天后过期' : '即将过期')) + '</dd>'
    + '<dt>来源</dt><dd style="color:var(--sub)">' + from + '</dd>';
}

function esc(s){ return String(s).replace(/[&<>"]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m])); }

$('btnStart').onclick = async () => {
  $('btnStart').disabled = true;
  await fetch('/api/start', {method:'POST'});
  setTimeout(()=>{ $('btnStart').disabled = false; refresh(); }, 2500);
};
$('btnStop').onclick = async () => {
  $('btnStop').disabled = true;
  await fetch('/api/stop', {method:'POST'});
  setTimeout(()=>{ $('btnStop').disabled = false; refresh(); }, 600);
};

refresh();
setInterval(refresh, 4000);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 转换器子进程管理
# ---------------------------------------------------------------------------

def _is_alive(proc) -> bool:
    return proc is not None and proc.poll() is None


def _start():
    with _state_lock:
        if _is_alive(_state["proc"]):
            return {"ok": True, "msg": "已在运行"}
        log_path = _state["log_path"]
        args = [sys.executable, str(CONVERTER), "--port", str(_state["port"])]
        if log_path:
            args += ["--log", str(log_path)]
        args += _state["extra_args"]
        try:
            logf = open(log_path, "a", encoding="utf-8") if log_path else subprocess.DEVNULL
            _state["proc"] = subprocess.Popen(
                args, stdout=logf, stderr=subprocess.STDOUT, cwd=str(HERE))
            _state["started_at"] = time.time()
            _state["last_error"] = ""
        except Exception as e:
            _state["last_error"] = f"启动失败：{e}"
            return {"ok": False, "msg": _state["last_error"]}
        return {"ok": True, "msg": "已启动"}


def _stop():
    with _state_lock:
        proc = _state["proc"]
        if not _is_alive(proc):
            _state["proc"] = None
            return {"ok": True, "msg": "未在运行"}
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception as e:
            _state["last_error"] = f"停止失败：{e}"
            return {"ok": False, "msg": _state["last_error"]}
        finally:
            _state["proc"] = None
        return {"ok": True, "msg": "已停止"}


def _tail_log(n: int = 60) -> str:
    p = _state["log_path"]
    if not p or not Path(p).is_file():
        return ""
    try:
        lines = Path(p).read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return ""


def _probe_converter() -> dict:
    """直接问转换器的 /health，拿账号与凭据状态。"""
    url = f"http://127.0.0.1:{_state['port']}/health"
    try:
        r = httpx.get(url, timeout=3)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def _uptime() -> str:
    if not _is_alive(_state["proc"]):
        return ""
    s = int(time.time() - _state["started_at"])
    if s < 60:
        return f"{s} 秒"
    if s < 3600:
        return f"{s // 60} 分钟"
    return f"{s // 3600} 小时 {(s % 3600) // 60} 分"


# ---------------------------------------------------------------------------
# 凭据快照：面板自己读，不依赖转换器是否在运行
# ---------------------------------------------------------------------------

_cred_lock = threading.Lock()
_cred_cache: dict = {"mtime": object(), "credential": None, "error": None}
_cred_warm_lock = threading.Lock()


def _cred_snapshot(fresh: bool = False) -> tuple[dict | None, str | None]:
    """返回 (凭据摘要, 错误信息)。按 auth 文件 mtime 缓存，避免每次轮询都 fork 客户端。

    解密要调一次桌面端 Electron 二进制（几百毫秒），所以必须缓存。
    """
    path = find_auth_file()
    if path is None:
        return None, "未找到登录文件：请先在 WorkBuddy / CodeBuddy 桌面端登录"

    try:
        mt = path.stat().st_mtime
    except OSError:
        mt = None

    with _cred_lock:
        if not fresh and mt is not None and _cred_cache["mtime"] == mt:
            return _cred_cache["credential"], _cred_cache["error"]

    try:
        cred = CredentialManager(path).summary()
        err = None
    except Exception as e:
        cred, err = None, str(e)

    with _cred_lock:
        _cred_cache.update(mtime=mt, credential=cred, error=err)
    return cred, err


def warm_credentials() -> None:
    """启动时在后台线程预热一次，避免首屏等解密。幂等。"""
    def _run():
        try:
            _cred_snapshot(fresh=True)
        except Exception:
            pass
        finally:
            _cred_warm_lock.release()

    if _cred_warm_lock.acquire(blocking=False):
        threading.Thread(target=_run, daemon=True, name="wb-cred-warmup").start()


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


@app.get("/api/status")
def status():
    running = _is_alive(_state["proc"])
    health = _probe_converter() if running else {}

    # 凭据：转换器在跑就优先用它的（token 可能已被刷新），否则面板自己读。
    cred = health.get("credential")
    err = health.get("credential_error")
    source = "converter"
    if not cred and not err:
        cred, err = _cred_snapshot()
        source = "panel"

    return {
        "running": running,
        "uptime": _uptime(),
        "port": _state["port"],
        "log": _tail_log(),
        "last_error": _state["last_error"],
        "credential": cred,
        "credential_error": err,
        "credential_source": source,
        "user_agent": health.get("user_agent"),
        "device_token": health.get("device_token"),
    }


@app.post("/api/start")
def api_start():
    return JSONResponse(_start())


@app.post("/api/stop")
def api_stop():
    return JSONResponse(_stop())


def main():
    global PANEL_PORT
    ap = argparse.ArgumentParser(description="wb-openai Web 控制面板")
    ap.add_argument("--panel-port", type=int, default=PANEL_PORT, help="面板端口（默认 8788）")
    ap.add_argument("--port", type=int, default=CONVERTER_PORT, help="转换器端口（默认 8787）")
    ap.add_argument("--log", default=str(Path.home() / "wb-openai.log"), help="转换器日志路径")
    ap.add_argument("--device-token", action="store_true", help="开启时附带设备风控头")
    ap.add_argument("--desensitize", action="store_true", help="开启时启用脱敏")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    PANEL_PORT = args.panel_port
    _state["port"] = args.port
    _state["log_path"] = args.log
    if args.device_token:
        _state["extra_args"].append("--device-token")
    if args.desensitize:
        _state["extra_args"].append("--desensitize")

    url = f"http://127.0.0.1:{PANEL_PORT}"
    print("=" * 56)
    print("  wb-openai 控制面板")
    print(f"  面板地址   : {url}")
    print(f"  转换器端口 : {args.port}（由面板拉起，未自动启动）")
    print(f"  日志文件   : {args.log}")
    print("  在页面上点「开启服务」即可。按 Ctrl+C 退出面板。")
    print("=" * 56)

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    # 后台预热凭据，让首屏不用等解密
    warm_credentials()

    uvicorn.run(app, host="127.0.0.1", port=PANEL_PORT, log_level="warning")


if __name__ == "__main__":
    main()
