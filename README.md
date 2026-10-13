# WorkBuddy 订阅 → DeepSeek Harness

**让你的本地 agent 用上 WorkBuddy 的积分。**

把本机已登录的 WorkBuddy / CodeBuddy 桌面端订阅，变成一个标准的 OpenAI 兼容服务，
再接入 **DeepSeek Harness（dsh）** —— 之后 dsh 跑的就是 WorkBuddy 积分里的
GLM-5.2 / Kimi / DeepSeek V4。

**dsh 有两种用法，配置方式完全不同**：
🖥️ **桌面端 App** 在界面里手动配（[看这里](#21-桌面端-appgui-必看)）；
⌨️ **命令行 / 服务端** 跑一条脚本（[看这里](#22-命令行--服务端-一键脚本)）。

自带 **Web 控制面板**，双击启动、页面上一个按钮开关，不用碰命令行。
支持 **macOS / Windows / Linux**。

> ⚠️ **不支持 Codex CLI**，原因见下方专章。也别指望 Claude Code —— 同样被拦。

---

## 一、30 秒上手

### 1. 起网关

| 平台 | 操作 |
|---|---|
| **macOS** | 双击 **`启动面板.command`** |
| **Windows** | 双击 **`start-panel.bat`** |

浏览器自动打开控制面板 → 点「**开启服务**」。首次运行会自动建 `.venv` 装依赖
（fastapi / uvicorn / httpx / cryptography），不污染系统 Python。

> macOS 首次双击如提示"来自身份不明的开发者"：右键点文件 → 选「打开」→ 再点「打开」。
> Windows 需先装 Python 3.8+（[python.org](https://www.python.org/downloads/)），安装时勾选 **Add Python to PATH**。

### 2. 接进 dsh

**dsh 有两种用法，配置方式完全不同，别搞混：**

| 你用哪个 | 怎么配 | 跳到 |
|---|---|---|
| 🖥️ **桌面端 App**（图形界面，双击图标打开那个） | 在 App 的 **设置 → 模型** 里手动填 | [2.1](#21-桌面端-appgui-必看) |
| ⌨️ **命令行 / 服务端**（`dsh web`、`dsh headless`） | 跑一条脚本 | [2.2](#22-命令行--服务端-一键脚本) |

> **桌面端不要用脚本。** 桌面端用的 `desktop` profile 由 Electron 应用独占管理，
> 脚本默认不碰它，跑了也没用，还容易把配置搞乱。

### 3. 用

```bash
dsh headless "读一下 a.txt 并总结"     # 一次性任务（命令行）
dsh web                                 # 浏览器界面（命令行）
```

桌面端就正常打开 App 用。

---

## 二、接入 DeepSeek Harness（dsh）

### 2.1 桌面端 App（GUI）—— 必看

> ⚠️ **桌面端的配置只能在这个 App 里改。**
> 它用的是 `desktop` profile，由 Electron 应用**独占管理** —— 用命令行访问会直接报
> `profile "desktop" is managed exclusively by the Electron application`。
> **也千万不要手改** `~/.dsh/profiles/desktop/cordis.patch.yml`：
> App 每次保存都会**整份重写**这个文件，你的手改会丢。

**怎么配**：打开桌面端 App → **设置 → 模型 → 添加模型提供商**，按下表填：

| 字段 | 填什么 |
|---|---|
| 显示名称 | 随便，例如 `workbuddy` |
| API 地址 | `http://127.0.0.1:8787/v1` |
| **API 协议** | **必须选 `OpenAI Chat Completions`** |
| **API Key** | **必须填**。网关不校验鉴权，填 `1` 都行 —— 但**留空一定失败** |
| 模型 → ID | 网关认识的真实模型名，如 `glm-5.2`、`deepseek-v4-flash`、`kimi-k2.7` |
| 模型 → 名称 | 随便，只用于显示 |

想一次配多个模型，点「**添加模型**」加行即可（同一个地址 + 协议，模型 ID 各填一行）。

保存后，在 App 的对话界面把模型切到刚配的那个就能用。

#### ⚠️ 三个高频错误

| 症状 | 原因 | 改法 |
|---|---|---|
| `404 {"detail":"Not Found"}` | 协议选成了 **`Anthropic Messages`** | 客户端会去打 `/v1/messages`，**网关没这个端点**。改成 `OpenAI Chat Completions` |
| `PI_AI_ERROR: No API key for provider: xxx` | **API Key 字段留空** | 填个值（随便填）。dsh 强制要求该字段存在，哪怕网关根本不校验 |
| 手改的配置莫名消失 | 手改了 `cordis.patch.yml` | App 保存时会整份重写。改配置一律在 App 里改 |

> 下拉里三个选项对应的实际协议：`OpenAI Chat Completions` → `openai-completions`、
> `OpenAI Responses` → `openai-responses`、`Anthropic Messages` → `anthropic-messages`。
> **前两个都能用**，第三个不行。

#### 配完自检

```bash
cd ~/wb-openai
python3 check-dsh.py --profile desktop
```

会逐项报协议、API Key、模型名、默认模型对不对，有问题直接说是哪儿错、怎么改。

---

### 2.2 命令行 / 服务端 —— 一键脚本

> 这一段**只适用于** `dsh web` / `dsh headless`。桌面端请回 [2.1](#21-桌面端-appgui-必看)。

```bash
python3 setup-dsh.py --profiles web,headless --set-default
```

脚本做三件事（**每步都先备份成 `*.bak-wb`**）：

1. 在 dsh 的 profile 补丁层声明 `wb-local` 路由 → `http://127.0.0.1:8787/v1`
2. 在 `~/.dsh/.credentials.yaml` 的 `refs` 里补一个占位 key
   （网关默认不校验鉴权，但 dsh 要求该字段必须存在，否则报 `No API key for provider`）
3. `--set-default` 时把 `agent-default-model` 指向 `wb-local / glm-5.2`

还原 / 参数：

```bash
python3 setup-dsh.py --restore --profiles web,headless    # 还原
python3 setup-dsh.py                                       # 只声明路由，不改默认模型
python3 setup-dsh.py --set-default --profiles headless     # 只处理某个 profile
```

---

### 2.3 配置体检

```bash
python3 check-dsh.py                    # 检查所有 profile
python3 check-dsh.py --profile desktop
```

只读，不改任何文件。会检查：协议对不对、`apiKeyEnv` 有没有且凭据存不存在、
模型名网关认不认识、默认模型指向是否有效。

---

### 2.4 实测结果（2026-10-13，macOS）

| 检查项 | 结果 |
|---|---|
| 桌面端 App 配置（协议 / Key / 模型 / 默认） | ✅ `check-dsh.py` 全绿 |
| `dsh headless "只回复两个字：在的"` | ✅ 返回「在的」 |
| 完整 agent 任务（读 a.txt → 建 b.txt → 写入内容） | ✅ 文件真的被创建、内容正确 |
| 请求规模 | 24 个工具 + 多轮对话，**未被上游风控拦** |
| `dsh --profile web --dump-config` 出现 `wb-local` | ✅ |
| `dsh web` 启动 + 模型选择器 | ✅ 可选「WorkBuddy 本地网关」 |

### 2.5 可选模型

网关支持的完整列表：`glm-5.2`、`glm-5.1`、`glm-5v-turbo`、`kimi-k2.7`、`kimi-k2.6`、
`kimi-k2.5`、`deepseek-v4-pro`、`deepseek-v4-flash`、`minimax-m3-pay`、`hy3-preview-agent`、`auto`。

命令行那套的 `wb-local` 路由默认声明了 GLM-5.2、GLM-5.1、DeepSeek V4 Pro、
DeepSeek V4 Flash、Kimi K2.7、auto；想加就在 `cordis.patch.yml` 的 `models` 列表里补一行，
或重跑 `setup-dsh.py`（幂等）。
桌面端在 App 里点「添加模型」加行即可。

---

## 三、⚠️ 不支持 Codex CLI

网关实现了 `/v1/responses`（Responses API），**协议层面 Codex CLI 能接通，
但实际发请求会被上游拦下**：

```
HTTP 400  {"code":11128, "msg":"Illegal API invocation from an unapproved channel"}
```

### 根因（已二分定位）

腾讯在后端**识别并拦截竞品 CLI 的客户端指纹**。Codex 系统提示开头这 185 个字符
是精准触发点：

> You are a coding agent running in the Codex CLI, a terminal-based coding assistant.
> Codex CLI is an open source project led by OpenAI.
> You are expected to be precise, safe, and helpful.

### 实验矩阵

| 测试 | 结果 |
|---|---|
| 三句连在一起（Codex 原文） | ❌ 被拦 |
| 任意两句组合 / 删掉任意一句 | ✅ 通过 |
| 前面加一句中性话 | ✅ 通过 |
| **Claude Code 的自我介绍** | ❌ 被拦 |
| Cursor 风格 / 通用 coding agent 声明 | ✅ 通过 |
| 800 字中性 system prompt | ✅ 通过 |
| 工具数量（1 个 vs 11 个 vs 24 个） | 无关 |
| 请求总长度 | 无关（截到 2000 字仍触发） |
| `--desensitize` 脱敏 | 无效（整串指纹匹配，非关键词） |

**这是整串指纹匹配，不是关键词过滤。** 三句必须连在一起，缺一句就过。

### 结论

**这不是协议问题，是腾讯主动的渠道管控。** 错误原文就是 "unapproved channel"。

让 Codex 走这个订阅，只能改写客户端系统提示去规避识别 —— 那属于绕风控，
本项目不做，也不建议（账号有被限制的风险，且是长期猫鼠游戏）。

**想用 Codex 的话**：继续用你现有的第三方 provider。

### 其他 harness 能用吗？

关键不在"哪个 harness"，而在**它的系统提示是否带竞品指纹**。实测：

| harness 系统提示风格 | 结果 |
|---|---|
| DeepSeek Harness | ✅ 通过（已完整实测） |
| Aider / Cline / OpenHands / Gemini CLI / Continue 风格 | ✅ 通过 |
| 中文通用编程助手声明 | ✅ 通过 |
| **Codex CLI 官方提示** | ❌ 被拦 |
| **Claude Code 官方提示** | ❌ 被拦 |

> 上表除 dsh 外是**风格模拟测试**，不是各工具官方提示的逐字复现 ——
> 指纹匹配对措辞敏感，官方原文可能与我模拟的不同。接入前建议先跑一次验证。

判断方法：接上后随便发一句，若报 `code 11128` 就是被识别了，否则能用。

---

## 四、其他兼容客户端

网关本身是通用 OpenAI 兼容服务，任何走 `/v1/chat/completions` 的客户端都能接：

| 项 | 值 |
|---|---|
| API Base | `http://127.0.0.1:8787/v1` |
| API Key | 留空（未启用鉴权） |
| 模型名 | `glm-5.2` / `kimi-k2.7` / `deepseek-v4-pro` / `auto` 等 |

樱桃（Cherry Studio）、NextChat、LobeChat、Open WebUI、任意 OpenAI SDK 都行。
这些客户端的 system prompt 不含竞品指纹，不受上述拦截影响。

---

## 五、控制面板

面板常驻 `127.0.0.1:8788`，转换器在 `127.0.0.1:8787`，由面板拉起和关停。

| 页面元素 | 说明 |
|---|---|
| 状态灯 + 开启/关闭按钮 | 一键启停转换器 |
| 账号与凭据 | 账号、uid、token 类型与有效期（不显示 token 内容；服务关着也能看） |
| 客户端接入 | 展示 API Base，带一键复制 |
| 运行日志 | 实时显示转换器输出 |

```bash
python3 wb_panel.py                     # 默认 8788 + 自动开浏览器
python3 wb_panel.py --no-browser        # 不开浏览器
python3 wb_panel.py --port 9000         # 换转换器端口
python3 wb_panel.py --device-token      # 开启时附设备风控头
python3 wb_panel.py --desensitize       # 开启时启用脱敏
```

---

## 六、实测状态（2026-10-13，macOS + WorkBuddy 5.7.7）

| 能力 | 结果 |
|---|---|
| 凭据解密（新版加密信封） | ✅ |
| `GET /health` / `GET /v1/models` | ✅ |
| 非流式 / 流式 SSE | ✅ usage 完整 |
| Function Calling（`finish_reason: tool_calls`） | ✅ |
| 多轮工具结果回传 | ✅ |
| `/v1/responses`（Responses API） | ✅ 事件序列完整 |
| 多模型切换 | ✅ |
| 桌面端自动刷新 token 后 | ✅ 自动重新解密，无需重启 |
| 面板一键启停 | ✅ |
| 依赖自举（空环境） | ✅ 自动建 venv、装依赖、起面板 |
| dsh 完整 agent 流程 | ✅ |

---

## 七、原理与实现取舍

### 它怎么工作

```
dsh / OpenAI 兼容客户端
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

**3. 设备风控头默认关闭。** 实测普通对话和 agent 请求都不需要 `X-Device-Token`。
该头要调桌面端自带的 TuringShield SDK（`native/turing-sdk`），在某些环境会超时失败。

**4. 只监听 127.0.0.1。** 不对外暴露。要局域网/公网用，自己决定风险。

---

## 八、命令行用法（不用面板）

```bash
python3 converter.py --port 8787 --log ~/wb-openai.log
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--host` / `--port` | `127.0.0.1` / `8787` | 监听地址与端口 |
| `--api-key` | 无 | 启用客户端鉴权（也可用环境变量 `CODEBUDDY2OPENAI_KEY`） |
| `--log PATH` | 无 | 写日志，含请求 ID / 模型 / 耗时 / finish_reason / token 数 |
| `--default-model` | `auto` | 未知模型名（如 Codex 发来的 `gpt-5-codex`）回落到该模型 |
| `--device-token` | 关 | 注入 `X-Device-Token` 设备风控头 |
| `--desensitize` | 关 | 对 system 里合规敏感词插零宽空格 |
| `--dump-body` | 关 | 把每个请求的完整 body 写进日志（排障用，含对话内容） |
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

## 九、常见问题

**dsh 报连接失败** —— 网关没起。先双击启动面板并点「开启服务」。

**dsh 报 `404 {"detail":"Not Found"}`** —— 协议选错了，八成选了 `Anthropic Messages`。
改成 `OpenAI Chat Completions`。见 [2.1](#21-桌面端-appgui-必看)。

**dsh 报 `PI_AI_ERROR: No API key for provider`** —— API Key 字段留空了，
或 `apiKeyEnv` 指向的凭据不存在。桌面端在 App 里填上；命令行重跑 `setup-dsh.py`。

**`TRANSPORT: Connection error`** —— 连不上网关。先确认面板里是「运行中」，
再 `curl http://127.0.0.1:8787/health` 看看。

**不确定哪儿配错了** —— `python3 check-dsh.py` 直接告诉你。

**找不到登录文件** —— 桌面端要**登录进去**，不是只装。
路径：macOS `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/*.info`；
Windows `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\*.info`。

**提示"无法从客户端读取解密能力"** —— 确认桌面端装在默认位置
（macOS `/Applications/WorkBuddy.app`，Windows `C:\WorkBuddy`）。
装到别处可设环境变量 `WORKBUDDY_TURING_SDK_DIR` 指向 `native/turing-sdk` 目录。

**客户端报 401** —— token 失效。在桌面端重新登录一次即可。

**报 `code 11128`** —— 被上游识别为竞品客户端。换客户端（见第三章）。

**响应慢** —— 换 `deepseek-v4-flash`。

---

## 十、文件

```
wb-openai/
├── 启动面板.command    # macOS 双击启动
├── start-panel.bat     # Windows 双击启动
├── bootstrap.py        # 依赖自举（建 venv + 装依赖 + 转发执行）
├── wb_panel.py         # Web 控制面板
├── converter.py        # 转换器主程序
├── wb_decode.py        # 凭据解密模块
├── responses_adapter.py# Responses API ↔ Chat 适配（来自 xiaofan6ya/workbuddy2api, MIT）
├── setup-dsh.py        # DeepSeek Harness 一键接入（命令行 profile）
├── check-dsh.py        # dsh 配置体检（只读，桌面端也能查）
├── desensitize.py      # 脱敏模块（可选）
├── turing_helper.js    # 设备风控头助手（可选）
└── start.sh            # 纯命令行启动
```

---

## 十一、免责声明

个人学习研究用途，非官方产品，与腾讯 / DeepSeek / OpenAI 无任何关联。
仅在拥有合法订阅的前提下自用，遵守相关服务条款，自负风险。

`responses_adapter.py` 来自 [xiaofan6ya/workbuddy2api](https://github.com/xiaofan6ya/workbuddy2api)（MIT License,
Copyright (c) 2026 HanHan666666 / Chris），文件头已保留署名。
