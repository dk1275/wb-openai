#!/usr/bin/env python3
"""
check-dsh.py — 体检 dsh 的 wb-openai 配置，指哪儿错了。

只读，不改任何文件。

用法：
  python3 check-dsh.py                 # 检查所有 profile
  python3 check-dsh.py --profile desktop
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

DSH_HOME = Path.home() / ".dsh"
GATEWAY = "http://127.0.0.1:8787/v1"
# 网关只讲 OpenAI 协议，没有 /v1/messages
OUR_PROTOCOLS = {"openai-completions", "openai-responses"}
PROTOCOL_HINT = {
    "openai-completions": "OpenAI Chat Completions",
    "openai-responses": "OpenAI Responses",
    "anthropic-messages": "Anthropic Messages",
    "deepseek": "DeepSeek",
}

OK, WARN, BAD = "✅", "⚠️ ", "❌"


def load_patch(profile: str):
    p = DSH_HOME / "profiles" / profile / "cordis.patch.yml"
    if not p.is_file():
        return None
    body = "\n".join(l for l in p.read_text().splitlines()
                     if not l.strip().startswith("#"))
    try:
        entries = yaml.safe_load(body) or []
    except Exception as e:
        print(f"  {BAD} 解析失败: {e}")
        return None
    return entries if isinstance(entries, list) else None


def entries_of(entries, entry_id):
    return [e for e in entries if isinstance(e, dict) and e.get("id") == entry_id]


def check_profile(profile: str) -> int:
    print(f"\n=== profile: {profile} ===")
    if not (DSH_HOME / "profiles" / profile).is_dir():
        print(f"  {WARN}未初始化（该 profile 还没被用过），跳过")
        return 0

    entries = load_patch(profile)
    if entries is None:
        print(f"  {BAD} 读不到补丁层")
        return 1

    problems = 0

    # --- provider 检查 ---
    providers = {}
    for e in entries_of(entries, "llm-pi-ai"):
        providers.update((e.get("config") or {}).get("providers") or {})

    ours = {k: v for k, v in providers.items()
            if isinstance(v, dict) and GATEWAY in str(v.get("baseURL", ""))}
    if not ours:
        print(f"  {WARN}没有指向本网关（{GATEWAY}）的 provider")
        if providers:
            print(f"      现有的：{', '.join(providers)}")
        return 0

    for name, cfg in ours.items():
        print(f"  provider `{name}`:")
        if cfg.get("displayName"):
            print(f"     显示名  : {cfg['displayName']}")

        proto = cfg.get("api")
        if proto in OUR_PROTOCOLS:
            print(f"     {OK} 协议    : {proto}（{PROTOCOL_HINT.get(proto, proto)}）")
        elif proto == "anthropic-messages":
            print(f"     {BAD} 协议    : anthropic-messages（Anthropic Messages）")
            print(f"         → 客户端会请求 /v1/messages，网关没有这个端点，必然 404")
            print(f"         → 改成 OpenAI Chat Completions")
            problems += 1
        else:
            print(f"     {BAD} 协议    : {proto!r} 不是网关支持的协议")
            problems += 1

        key_ref = cfg.get("apiKeyEnv")
        if key_ref:
            cred_file = DSH_HOME / ".credentials.yaml"
            have = False
            if cred_file.is_file():
                refs = (yaml.safe_load(cred_file.read_text()) or {}).get("refs") or {}
                have = key_ref in refs
            if have:
                print(f"     {OK} API Key : {key_ref}（凭据已存在）")
            else:
                print(f"     {BAD} API Key : 配置里写了 {key_ref}，但 ~/.dsh/.credentials.yaml 里没有")
                problems += 1
        else:
            derived = name.upper().replace("-", "_").replace(".", "_") + "_API_KEY"
            print(f"     {BAD} API Key : 配置里没有 apiKeyEnv")
            print(f"         → 运行时会报 PI_AI_ERROR: No API key for provider: {name}")
            print(f"         → 在桌面端 UI 的「API Key」字段里填个值（随便填，网关不校验）")
            print(f"         → UI 派生的引用名一般是 {derived}")
            problems += 1

        models = cfg.get("models") or []
        if models:
            ids = [m.get("id") for m in models if isinstance(m, dict)]
            print(f"     {OK} 模型    : {', '.join(str(i) for i in ids)}")
            if not any(i in ("glm-5.2", "glm-5.1", "glm-5v-turbo", "kimi-k2.7", "kimi-k2.6",
                             "kimi-k2.5", "deepseek-v4-pro", "deepseek-v4-flash",
                             "minimax-m3-pay", "hy3-preview-agent", "auto")
                       for i in ids if isinstance(i, str)):
                print(f"         ⚠️  没有一个是网关认识的模型名，必然失败")
                problems += 1
        else:
            print(f"     {WARN}模型    : 没声明（会用内置 catalog，本网关不在 catalog 里）")

    # --- 默认模型检查 ---
    for e in entries_of(entries, "agent-default-model"):
        cfg = e.get("config") or {}
        prov = cfg.get("provider")
        model = cfg.get("model")
        if prov in ours:
            print(f"  {OK} 默认模型: {prov} / {model}")
        elif prov in providers:
            print(f"  {WARN}默认模型: {prov} / {model}（不是本网关，是有意的就忽略）")
        else:
            print(f"  {BAD} 默认模型指向不存在的 provider: {prov}")
            problems += 1

    return problems


def main():
    ap = argparse.ArgumentParser(description="体检 dsh 的 wb-openai 配置")
    ap.add_argument("--profile", default=None, help="只查某个 profile")
    args = ap.parse_args()

    if not DSH_HOME.is_dir():
        print(f"未找到 {DSH_HOME}，dsh 似乎没装。")
        return 1

    profiles = [args.profile] if args.profile else \
        sorted(p.name for p in (DSH_HOME / "profiles").iterdir()
               if p.is_dir() and p.name != "node_modules")

    total = 0
    for name in profiles:
        total += check_profile(name)

    print("\n" + "=" * 52)
    if total == 0:
        print(f"{OK} 没发现问题")
    else:
        print(f"{BAD} 共 {total} 处问题，按上面提示改")
    print("提示：桌面端（desktop）必须在 App 的 设置 → 模型 里改，")
    print("      不要手改 cordis.patch.yml —— 应用保存时会整份重写。")
    return 0 if total == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
