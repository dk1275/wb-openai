#!/usr/bin/env python3
"""
setup-dsh.py — 把本地 wb-openai 网关接进 DeepSeek Harness (dsh)。

做三件事（都会先备份原文件）：
  1. 在 dsh 的 profile 补丁层里声明一个名为 wb-local 的 provider 路由，
     指向 http://127.0.0.1:8787/v1（openai-completions 协议）。
  2. 在 ~/.dsh/.credentials.yaml 的 refs 里补一个占位 key
     （网关默认不校验鉴权，但 dsh 要求该字段必须存在）。
  3. 可选：把 agent-default-model 指向 wb-local（headless 等没有模型选择器的
     profile 需要；web/desktop 有 UI 选择器，一般不用改）。

用法：
  python3 setup-dsh.py                 # 声明路由，不改默认模型
  python3 setup-dsh.py --set-default   # 同时把默认模型设为 wb-local/glm-5.2
  python3 setup-dsh.py --profiles web,headless
  python3 setup-dsh.py --restore       # 从 .bak-wb 备份还原

前提：dsh 已安装（npm i -g @deepseek-ai/dsh），且 wb-openai 网关已能启动。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

DSH_HOME = Path.home() / ".dsh"
PROFILES = ("web", "headless")
API_KEY_REF = "WB_LOCAL_API_KEY"
API_KEY_PLACEHOLDER = "wb-local-gateway"

HEADER = (
    "# Your patch layer for this dsh profile, applied after every bundle layer:\n"
    "# a top-level YAML array of loader patch entries (id-targeted config\n"
    "# overrides, disables, and insert lists; `!!js` expressions allowed).\n"
    "#\n"
    "# wb-local 由 setup-dsh.py 写入，指向本地 wb-openai 网关。\n"
    "# 启动网关：cd ~/wb-openai && ./启动面板.command → 点「开启服务」\n"
)

PROVIDER_ENTRY = {
    "id": "llm-pi-ai",
    "config": {
        "providers": {
            "wb-local": {
                "displayName": "WorkBuddy 本地网关",
                "api": "openai-completions",
                "baseURL": "http://127.0.0.1:8787/v1",
                "apiKeyEnv": API_KEY_REF,
                "defaultContextWindow": 131072,
                "defaultMaxTokens": 16384,
                "models": [
                    {"id": "glm-5.2", "name": "GLM-5.2"},
                    {"id": "glm-5.1", "name": "GLM-5.1"},
                    {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro"},
                    {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash"},
                    {"id": "kimi-k2.7", "name": "Kimi K2.7"},
                    {"id": "auto", "name": "Auto"},
                ],
            }
        }
    },
}

DEFAULT_MODEL_ENTRY = {
    "id": "agent-default-model",
    "config": {"provider": "wb-local", "model": "glm-5.2"},
}


def load_entries(patch: Path) -> list:
    body = "\n".join(l for l in patch.read_text().splitlines()
                     if not l.strip().startswith("#"))
    entries = yaml.safe_load(body) or []
    if not isinstance(entries, list):
        raise ValueError(f"{patch} 顶层不是数组，未按预期格式，已跳过")
    return entries


def write_entries(patch: Path, entries: list) -> None:
    patch.write_text(HEADER + yaml.safe_dump(entries, allow_unicode=True, sort_keys=False))


def strip_entry(entries: list, entry_id: str) -> list:
    """去掉 setup 自己写过的同名条目（幂等）。"""
    return [e for e in entries
            if not (isinstance(e, dict) and e.get("id") == entry_id)]


def ensure_credential() -> None:
    cred = DSH_HOME / ".credentials.yaml"
    if not cred.is_file():
        print(f"  ! 未找到 {cred}，跳过凭据写入")
        return
    shutil.copy2(cred, cred.with_suffix(".yaml.bak-wb"))
    data = yaml.safe_load(cred.read_text()) or {}
    data.setdefault("refs", {})
    if API_KEY_REF in data["refs"]:
        print(f"  · refs.{API_KEY_REF} 已存在，跳过")
    else:
        data["refs"][API_KEY_REF] = API_KEY_PLACEHOLDER
        cred.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        cred.chmod(0o600)
        print(f"  ✓ 已写入 refs.{API_KEY_REF}")


def restore(profiles: tuple[str, ...]) -> None:
    n = 0
    for name in profiles:
        patch = DSH_HOME / "profiles" / name / "cordis.patch.yml"
        bak = patch.with_suffix(".yml.bak-wb")
        if bak.is_file():
            shutil.copy2(bak, patch)
            print(f"  ✓ 已还原 {name}")
            n += 1
        else:
            print(f"  · {name} 无备份，跳过")
    cred = DSH_HOME / ".credentials.yaml"
    cbak = cred.with_suffix(".yaml.bak-wb")
    if cbak.is_file():
        shutil.copy2(cbak, cred)
        print("  ✓ 已还原凭据文件")
    print(f"共还原 {n} 个 profile")


def main():
    ap = argparse.ArgumentParser(description="把 wb-openai 网关接进 DeepSeek Harness")
    ap.add_argument("--profiles", default=",".join(PROFILES),
                    help=f"要处理的 profile，逗号分隔（默认 {','.join(PROFILES)}）")
    ap.add_argument("--set-default", action="store_true",
                    help="同时把 agent-default-model 指向 wb-local/glm-5.2")
    ap.add_argument("--restore", action="store_true", help="从 .bak-wb 备份还原")
    args = ap.parse_args()

    profiles = tuple(p.strip() for p in args.profiles.split(",") if p.strip())

    if not DSH_HOME.is_dir():
        print(f"未找到 {DSH_HOME}，请先安装并运行一次 dsh。")
        return 1

    if args.restore:
        print("还原中…")
        restore(profiles)
        return 0

    print("接入 wb-openai 网关到 dsh：")
    for name in profiles:
        patch = DSH_HOME / "profiles" / name / "cordis.patch.yml"
        if not patch.is_file():
            print(f"  · {name}: 无 patch 文件，跳过（该 profile 未初始化）")
            continue
        bak = patch.with_suffix(".yml.bak-wb")
        if not bak.exists():
            shutil.copy2(patch, bak)
        try:
            entries = strip_entry(load_entries(patch), "llm-pi-ai")
            entries.append(PROVIDER_ENTRY)
            if args.set_default:
                entries = strip_entry(entries, "agent-default-model")
                entries.append(DEFAULT_MODEL_ENTRY)
            write_entries(patch, entries)
            extra = " + 默认模型" if args.set_default else ""
            print(f"  ✓ {name}: 已声明 wb-local 路由{extra}")
        except Exception as e:
            print(f"  ✗ {name}: {e}")

    print("\n凭据：")
    ensure_credential()

    print("\n完成。验证：")
    print("  1) 启动网关： cd ~/wb-openai && ./启动面板.command → 点「开启服务」")
    print("  2) 跑一个任务：dsh headless \"只回复两个字：在的\"")
    print("     （未加 --set-default 时，用 dsh headless --patch <本目录>/dsh-wb-local.patch.yml）")
    print("  3) Web 界面： dsh web → 设置 → 模型，应能看到「WorkBuddy 本地网关」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
