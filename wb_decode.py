#!/usr/bin/env python3
"""wb_decode.py — 解密新版 WorkBuddy/CodeBuddy 桌面端的加密 .info 凭据。

原理（来自 xiaofan6ya/workbuddy2api 的 credential_codec.py，本机版支持 macOS/Linux/Windows）：
  1. 以 ELECTRON_RUN_AS_NODE=1 跑本机安装的 Electron 主二进制，调
     process._linkedBinding('electron_browser_workbuddy_storage').loggerGet()
     拿到 atRestSecretKey（base64 字符串）。
  2. key    = sha256(atRestSecretKey 字符串 utf8)
     keyId  = sha256(key).hexdigest()[:16]
  3. 信封 {suite:1, keyId, nonce(12B), authTag(16B), ciphertext} → AES-256-GCM，
     AAD 按官方 sym-v1/WBEV1 帧构造。
  4. 递归替换 JSON 里所有 {"$wbEncrypted":1,"envelope":"..."} 节点。

只读，不修改任何客户端文件。密钥仅存在于内存。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEY_SCRIPT = """
try {
  const crypto = require('node:crypto');
  const payload = JSON.parse(process._linkedBinding('electron_browser_workbuddy_storage').loggerGet());
  if (payload.version !== 1 || typeof payload.atRestSecretKey !== 'string') throw Error();
  const raw = Buffer.from(payload.atRestSecretKey, 'base64');
  if (raw.length !== 32 || raw.toString('base64') !== payload.atRestSecretKey || raw.every(x => x === 0)) throw Error();
  const key = crypto.createHash('sha256').update(payload.atRestSecretKey, 'utf8').digest();
  const keyId = crypto.createHash('sha256').update(key).digest('hex').slice(0, 16);
  process.stdout.write(JSON.stringify({keyId, key: key.toString('base64')}));
} catch (_) { process.exitCode = 1; }
"""


class DecodeError(ValueError):
    pass


def _find_electron() -> Path | None:
    """定位本机 WorkBuddy/CodeBuddy 的 Electron 主二进制。"""
    candidates: list[Path] = []
    if sys.platform == "darwin":
        for app in ("/Applications/WorkBuddy.app", "/Applications/CodeBuddy.app",
                    str(Path.home() / "Applications" / "WorkBuddy.app"),
                    str(Path.home() / "Applications" / "CodeBuddy.app")):
            candidates.append(Path(app) / "Contents" / "MacOS")
            if (Path(app) / "Contents" / "MacOS").is_dir():
                for f in (Path(app) / "Contents" / "MacOS").iterdir():
                    if f.is_file() and os.access(f, os.X_OK) and f.suffix == "":
                        return f
        # 兜底：扫 Applications 下所有同名 app
        root = Path("/Applications")
        if root.is_dir():
            for app in root.glob("*WorkBuddy*.app"):
                candidates.append(app / "Contents" / "MacOS")
            for app in root.glob("*CodeBuddy*.app"):
                candidates.append(app / "Contents" / "MacOS")
    elif os.name == "nt":
        for d in ("C:\\WorkBuddy", "C:\\workbuddy", os.environ.get("LOCALAPPDATA", "")):
            if not d:
                continue
            for name in ("WorkBuddy.exe", "CodeBuddy.exe"):
                candidates.append(Path(d) / name)
    else:
        for base in (Path("/opt/WorkBuddy"), Path("/opt/workbuddy"), Path("/usr/share/workbuddy")):
            candidates.append(base / "workbuddy")
            candidates.append(base / "WorkBuddy")

    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
        if c.is_dir():
            # 目录里挑可执行文件（Electron 主程序通常就叫 Electron 或与 app 同名）
            for f in sorted(c.iterdir()):
                if f.is_file() and os.access(f, os.X_OK) and f.suffix == "":
                    return f
            for name in ("WorkBuddy.exe", "CodeBuddy.exe"):
                if (c / name).is_file():
                    return c / name
    return None


def get_key() -> tuple[str, bytes]:
    """返回 (keyId, key)。失败抛 DecodeError。"""
    exe = _find_electron()
    if exe is None:
        raise DecodeError("未找到本机 WorkBuddy/CodeBuddy 客户端可执行文件")
    env = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
    env.pop("NODE_OPTIONS", None)
    try:
        r = subprocess.run([str(exe), "-e", _KEY_SCRIPT], env=env,
                           capture_output=True, timeout=20)
    except Exception as e:
        raise DecodeError(f"调用客户端二进制失败：{e}") from None
    if r.returncode or len(r.stdout) > 4096:
        raise DecodeError(f"无法从客户端读取解密能力（{exe.name}），请确认客户端已安装")
    try:
        value = json.loads(r.stdout)
        key = base64.b64decode(value["key"], validate=True)
        key_id = hashlib.sha256(key).hexdigest()[:16]
        if len(key) != 32 or value["keyId"] != key_id:
            raise ValueError()
        return key_id, key
    except Exception:
        raise DecodeError("解密密钥校验失败") from None


def _b64(value) -> bytes:
    if not isinstance(value, str):
        raise ValueError()
    decoded = base64.b64decode(value, validate=True)
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError()
    return decoded


def _aad(key_id: str) -> bytes:
    def text(v: str) -> bytes:
        e = v.encode("utf-8")
        return struct.pack(">I", len(e)) + e
    return (b"WB-AAD\0\x01" + text("WBEV1") + text("sym-v1") +
            struct.pack(">I", 1) + text(key_id) + b"\x02\x00\x00")


def _open_field(wrapper: dict, key_id: str, key: bytes) -> str:
    if wrapper.get("scheme") is not None:
        raise DecodeError("该凭证使用暂不支持的高级加密格式")
    try:
        if set(wrapper) != {"$wbEncrypted", "envelope"} or wrapper["$wbEncrypted"] != 1:
            raise ValueError()
        envelope = json.loads(_b64(wrapper["envelope"]))
        if (set(envelope) != {"suite", "keyId", "nonce", "authTag", "ciphertext"}
                or envelope["suite"] != 1):
            raise ValueError()
        if envelope["keyId"] != key_id:
            raise DecodeError("凭证加密版本与本机客户端不匹配，请更新客户端")
        nonce, tag, ct = (_b64(envelope[n]) for n in ("nonce", "authTag", "ciphertext"))
        if len(nonce) != 12 or len(tag) != 16:
            raise ValueError()
        return AESGCM(key).decrypt(nonce, ct + tag, _aad(key_id)).decode("utf-8")
    except DecodeError:
        raise
    except Exception as e:
        raise DecodeError(f"加密凭证损坏或校验失败：{e}") from None


def normalize_credential(raw: str) -> str:
    """把含加密字段的 .info JSON 还原成明文 JSON 字符串。"""
    try:
        data = json.loads(raw.lstrip("﻿"))
    except (ValueError, TypeError):
        return raw
    km: tuple[str, bytes] | None = None
    changed = False

    def visit(value, depth=0):
        nonlocal km, changed
        if depth > 32:
            raise DecodeError("凭证结构嵌套过深")
        if isinstance(value, dict):
            if "$wbEncrypted" in value:
                if km is None:
                    km = get_key()
                changed = True
                return _open_field(value, *km)
            return {k: visit(v, depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            return [visit(v, depth + 1) for _, v in enumerate(value)]
        return value

    decoded = visit(data)
    return json.dumps(decoded, ensure_ascii=False) if changed else raw.lstrip("﻿")


def _peek_key_id(raw: str) -> str:
    """从原始（未解密）文本里找出信封的 keyId，用于对照诊断。找不到返回 '(明文，无加密字段)'。"""
    try:
        data = json.loads(raw.lstrip("﻿"))

        def walk(v, depth=0):
            if depth > 32 or v is None:
                return None
            if isinstance(v, dict):
                if "$wbEncrypted" in v:
                    env = json.loads(_b64(v.get("envelope", "")))
                    return env.get("keyId")
                for x in v.values():
                    got = walk(x, depth + 1)
                    if got:
                        return got
            elif isinstance(v, list):
                for x in v:
                    got = walk(x, depth + 1)
                    if got:
                        return got
            return None

        return walk(data) or "(明文，无加密字段)"
    except Exception:
        return "(解析失败)"


def main():
    if len(sys.argv) < 2:
        print("用法: wb_decode.py <auth.info 路径> [--json]")
        print("  不带 --json 只打印脱敏摘要；带 --json 输出解密后的完整 JSON（含 token，慎外传）")
        return
    path = Path(sys.argv[1]).expanduser()
    raw = path.read_text(encoding="utf-8")
    key_id = _peek_key_id(raw)
    plain = normalize_credential(raw)
    data = json.loads(plain)
    if "--json" in sys.argv:
        print(plain)
        return
    auth = data.get("auth") or {}
    acct = data.get("account") or {}
    print("=== 解密成功 ===")
    print("文件      :", path)
    print("信封keyId :", key_id)
    print("nickname :", acct.get("nickname"))
    print("uid      :", acct.get("uid"))
    print("enterprise:", acct.get("enterpriseName"))
    print("tokenType:", auth.get("tokenType"))
    print("accessToken:", f"{auth.get('accessToken','')[:12]}… (len={len(auth.get('accessToken',''))})")
    print("refreshToken:", f"{auth.get('refreshToken','')[:12]}… (len={len(auth.get('refreshToken',''))})")
    exp = auth.get("expiresAt") or 0
    import time
    print("token过期 :", "是" if time.time() * 1000 >= exp - 60000 else "否",
          f"(剩余 {max(0, int((exp - time.time()*1000)/1000/60))} 分钟)")


if __name__ == "__main__":
    main()
