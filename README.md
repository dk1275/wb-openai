# wb-openai — WorkBuddy / CodeBuddy 订阅 → OpenAI 兼容 API

把你本机已登录的 **WorkBuddy / CodeBuddy 桌面端订阅**，变成一个标准的 OpenAI 兼容服务，
让 Cherry Studio / NextChat / LobeChat / Open WebUI / 任意 OpenAI SDK 客户端直接复用。

自带 **Web 控制面板**，双击启动、页面上一个按钮开关，不用碰命令行。
支持 **macOS / Windows / Linux**。

---

## 一、30 秒上手

### macOS

双击 **`启动面板.command`** → 浏览器自动打开控制面板 → 点「开启服务」。

> 首次双击如提示"无法打开，因为它来自身份不明的开发者"：右键点文件 → 选「打开」→ 再点「打开」。
> 或者终端里执行一次 `chmod +x 启动面板.command`。

### Windows

双击 **`start-panel.bat`** → 浏览器自动打开控制面板 → 点「开启服务」。

> 需要先装 Python 3.8+（[python.org](https://www.python.org/downloads/)），
> 安装时记得勾选 **Add Python to PATH**。

### 首次运行会做什么

自动在本目录建 `.venv` 并装依赖（fastapi / uvicorn / httpx / cryptography），
不污染系统 Python。装完自动起面板并打开浏览器。之后启动就是秒开。

### 客户端怎么填

| 项 | 值 |
|---|---|
| API Base | `http://127.0.0.1:8787/v1` |
| API Key | 留空（未启用鉴权） |
| 模型名 | `glm-5.2` / `kimi-k2.7` / `deepseek-v4-pro` / `deepseek-v4-flash` / `auto` 等 |

可用模型：`glm-5.2`、`glm-5.1`、`glm-5v-turbo`、`kimi-k2.7`、`kimi-k2.6`、`kimi-k2.5`、
`deepseek-v4-pro`、`deepseek-v4-flash`、`minimax-m3-pay`、`hy3-preview-agent`、`auto`

---

## ⚠️ 关于 Codex CLI（实测结论，2026-10-13）

网关已实现 `/v1/responses`（Responses API），**协议层面 Codex CLI 能接通**，
但实际发请求会被上游拦下：

```
HTTP 400  {"code":11128, "msg":"Illegal API invocation from an unapproved channel"}
```

**根因（已二分定位）**：腾讯在后端识别并拦截竞品 CLI 的客户端指纹。
Codex 系统提示开头这 185 个字符是精准触发点：

> You are a coding agent running in the Codex CLI, a terminal-based coding assistant.
> Codex CLI is an open source project led by OpenAI.
> You are expected to be precise, safe, and helpful.

实验矩阵：

| 测试 | 结果 |
|---|---|
| 三句连在一起（Codex 原文） | ❌ 被拦 |
| 任意两句组合 / 删掉任意一句 | ✅ 通过 |
| 前面加一句中性话 | ✅ 通过 |
| Claude Code 的自我介绍 | ❌ 被拦 |
| Cursor 风格 / 通用 coding agent 声明 | ✅ 通过 |
| 800 字中性 system prompt | ✅ 通过 |
| 工具数量（1 个 vs 11 个） | 无关 |
| 请求总长度 | 无关（截到 2000 字仍触发） |
| `--desensitize` 脱敏 | 无效（整串指纹匹配，非关键词） |

也就是说：**这不是协议问题，是腾讯主动的渠道管控**。
让 Codex 走这个订阅需要改写客户端系统提示去规避识别 —— 那属于绕风控，
本项目不做，也不建议（账号有被限制的风险，且是长期猫鼠游戏）。

**想用 Codex 的话**：继续用你现有的第三方 provider。本网关适用于
Cherry Studio / NextChat / LobeChat / Open WebUI 等走 `/v1/chat/completions`
的标准客户端，这些不受该指纹拦截影响。

---

## ✅ 接入 DeepSeek Harness（dsh）—— 实测可用

dsh 走标准 `openai-completions` 协议，且它的系统提示不含竞品指纹，
**实测完整 agent 流程全部通过**（发 24 个工具的请求、读文件、写文件都不被拦）。

### 一键接入

```bash
cd ~/wb-openai
python3 setup-dsh.py --profiles web,headless --set-default
```

脚本会（全部先备份为 `*.bak-wb`）：

1. 在 dsh 的 profile 补丁层声明 `wb-local` 路由 → `http://127.0.0.1:8787/v1`
2. 在 `~/.dsh/.credentials.yaml` 的 `refs` 补一个占位 key
   （网关默认不校验鉴权，但 dsh 要求该字段必须存在）
3. `--set-default` 时把 `agent-default-model` 指向 `wb-local / glm-5.2`

还原：`python3 setup-dsh.py --restore --profiles web,headless`

### 实测结果（2026-10-13）

| 检查项 | 结果 |
|---|---|
| `dsh --profile web --dump-config` 出现 `wb-local` | ✅ |
| `dsh headless "只回复两个字：在的"` | ✅ 返回「在的」 |
| 完整 agent 任务（读 a.txt → 建 b.txt → 写入内容） | ✅ 文件真的被创建、内容正确 |
| 请求规模 | 24 个工具 + 多轮，未被上游风控拦 |
| `dsh web` 启动 | ✅ 正常，模型选择器里可选「WorkBuddy 本地网关」 |

### 说明

- `desktop` profile 由 Electron 应用独占管理（`dsh --profile desktop` 会报
  "managed exclusively by the Electron application"），脚本默认不动它。
- 用 dsh 前记得先启动网关；网关没起 dsh 会报连接失败。
- 模型可在 dsh 的 设置 → 模型 里切换（`wb-local` 下有 GLM-5.2 / DeepSeek V4 /
  Kimi / auto）。headless 没有选择器，用 `--set-default` 或 `--patch` 指定。

---

## 二、控制面板

面板常驻在 `127.0.0.1:8788`，转换器在 `127.0.0.1:8787`，由面板拉起和关停。

| 页面元素 | 说明 |
|---|---|
| 状态灯 + 开启/关闭按钮 | 一键启停转换器 |
| 账号与凭据 | 显示账号、uid、token 类型与有效期（不显示 token 内容） |
| 客户端接入 | 直接展示 API Base，带一键复制 |
| 运行日志 | 实时显示转换器输出 |

面板参数：

```bash
python3 wb_panel.py                     # 默认 8788 + 自动开浏览器
python3 wb_panel.py --no-browser        # 不开浏览器
python3 wb_panel.py --port 9000         # 换转换器端口
python3 wb_panel.py --device-token      # 开启时附设备风控头
python3 wb_panel.py --desensitize       # 开启时启用脱敏
```

---

## 三、实测状态（2026-10-13，macOS + WorkBuddy 5.7.7）

| 能力 | 结果 |
|---|---|
| 凭据解密（新版加密信封） | ✅ |
| `GET /health` / `GET /v1/models` | ✅ |
| 非流式对话 | ✅ usage 完整 |
| 流式 SSE | ✅ 增量输出 |
| Function Calling | ✅ `finish_reason: tool_calls` |
| 多轮工具结果回传 | ✅ |
| 多模型切换 | ✅ `glm-5.2` / `deepseek-v4-flash` |
| 桌面端自动刷新 token 后 | ✅ 转换器自动重新解密，无需重启 |
| 面板一键启停 | ✅ 开启后即可对话，关闭后端口立即释放 |
| 依赖自举（空环境） | ✅ 自动建 venv、装依赖、起面板 |

---

## 四、原理与实现取舍

### 它怎么工作

```
OpenAI 兼容客户端
   │  POST /v1/chat/completions
   ▼
converter.py  (本地 FastAPI, 127.0.0.1:8787)
   │  解密本机凭据 → 注入 Authorization / X-User-Id 等头
   ▼
copilot.tencent.com/v2/chat/completions   ← 后端本身就是标准 OpenAI 协议
```

function calling 是后端原生能力（`tools` / `tool_calls`），不需要 prompt 注入或文本解析。

### 为什么需要解密

新版桌面端把 auth 文件里的敏感字段写成了加密信封：

```
auth.accessToken  : {"$wbEncrypted":1, "envelope":"eyJ..."}   ← 不是明文
auth.refreshToken : {"$wbEncrypted":1, "envelope":"..."}
account.nickname / phoneNumber : 同样加密
```

直接拼 `Bearer {accessToken}` 会得到 `Bearer {'$wbEncrypted': 1, ...}` —— 后端一律 401。

**解密流程：**

1. 用 `ELECTRON_RUN_AS_NODE=1` 跑本机安装的 Electron 主二进制，调
   `process._linkedBinding('electron_browser_workbuddy_storage').loggerGet()`
   拿 `atRestSecretKey`（base64 字符串）
2. `key = sha256(atRestSecretKey 字符串)`，`keyId = sha256(key).hexdigest()[:16]`
3. 信封 `{suite:1, keyId, nonce(12B), authTag(16B), ciphertext}` → AES-256-GCM，
   AAD 按官方 `WB-AAD\0\x01 + WBEV1 + sym-v1 + 1 + keyId` 帧构造
4. 递归替换 JSON 里所有 `$wbEncrypted` 节点

密钥只在进程内存里，不落盘、不写进任何文件。

### 四个设计取舍

**1. 不改客户端文件。** 全程只读 `.info`。即使 token 过期需要刷新，也只在内存里换新 token，
不会把明文写回客户端的加密文件（那会把桌面端搞坏）。桌面端自己刷新文件时，转换器靠
mtime 变化自动重新解密，无需重启。

**2. UA 用官方三段式。** `WorkBuddy/5.7.7 WorkBuddy/5.7.7 CLI/5.7.7`，版本号从安装包
`app.asar.unpacked/cli/product.json` 实读，不写死。

**3. 设备风控头默认关闭。** 实测普通对话不需要 `X-Device-Token`。该头要调桌面端自带的
TuringShield SDK（`native/turing-sdk`），在某些环境会超时失败。真被风控拦了再加 `--device-token`。

**4. 只监听 127.0.0.1。** 不对外暴露。要局域网/公网用，自己决定风险。

---

## 五、命令行用法（不用面板）

```bash
python3 converter.py --port 8787 --log ~/wb-openai.log
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--host` / `--port` | `127.0.0.1` / `8787` | 监听地址与端口 |
| `--api-key` | 无 | 启用客户端鉴权（也可用环境变量 `CODEBUDDY2OPENAI_KEY`） |
| `--log PATH` | 无 | 写日志，含请求 ID / 模型 / 耗时 / finish_reason / token 数 |
| `--device-token` | 关 | 注入 `X-Device-Token` 设备风控头 |
| `--desensitize` | 关 | 对 system 里合规敏感词插零宽空格，缓解后端误拦 |
| `--skip-check` | 否 | 跳过启动预检 |

验证：

```bash
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8787/v1/models
curl -N http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-5.2","stream":true,"messages":[{"role":"user","content":"你好"}]}'
```

---

## 六、常见问题

**找不到登录文件** —— 桌面端要**登录进去**，不是只装。
路径：macOS `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/*.info`；
Windows `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\*.info`。

**提示"无法从客户端读取解密能力"** —— 确认桌面端装在默认位置
（macOS `/Applications/WorkBuddy.app`，Windows `C:\WorkBuddy`）。
装到别处可设环境变量 `WORKBUDDY_TURING_SDK_DIR` 指向 `native/turing-sdk` 目录。

**客户端报 401** —— 转换器用的是后端 401，说明 token 失效。在桌面端重新登录一次即可。

**响应慢** —— 换 `deepseek-v4-flash`。

**"敏感内容"被拦截** —— 后端内容审核（腾讯合规策略）。常见误伤是客户端注入的 system
提示里含安全英文术语。用 `--desensitize`。

---

## 七、文件

```
wb-openai/
├── 启动面板.command    # macOS 双击启动
├── start-panel.bat     # Windows 双击启动
├── bootstrap.py        # 依赖自举（建 venv + 装依赖 + 转发执行）
├── wb_panel.py         # Web 控制面板
├── converter.py        # 转换器主程序
├── wb_decode.py        # 凭据解密模块
├── desensitize.py      # 脱敏模块（可选）
├── turing_helper.js    # 设备风控头助手（可选）
└── start.sh            # 纯命令行启动
```

---

## 八、免责声明

个人学习研究用途，非官方产品，与腾讯 / CodeBuddy / OpenAI 无关联。
仅在拥有合法订阅的前提下自用，遵守相关服务条款，自负风险。
