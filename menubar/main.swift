// WBOpenAI — macOS 菜单栏控制面板
//
// 把 wb-openai 网关做成菜单栏常驻应用：点图标查看状态、一键开启/关闭。
// 脚本资源打包在 App 的 Contents/Resources/wb-openai/ 内，首次启动同步到
// ~/.wb-openai/ 后运行，venv、日志都落在用户目录，App 本体保持只读。

import AppKit
import Foundation

// MARK: - 常量

let HOME = FileManager.default.homeDirectoryForCurrentUser
let WORK = HOME.appendingPathComponent(".wb-openai", isDirectory: true)
let LOG = WORK.appendingPathComponent("converter.log")
let PANEL_PORT = 8788
let CONVERTER_PORT = 8787

let fm = FileManager.default

func note(_ s: String) {
    FileHandle.standardError.write((s + "\n").data(using: .utf8)!)
}

// MARK: - 资源同步

/// 把 App 内置的脚本同步到 ~/.wb-openai/。不碰 .venv 和日志。
@discardableResult
func syncScripts() -> Bool {
    guard let res = Bundle.main.resourceURL?.appendingPathComponent("wb-openai") else {
        note("找不到内置资源目录")
        return false
    }
    try? fm.createDirectory(at: WORK, withIntermediateDirectories: true)
    guard let names = try? fm.contentsOfDirectory(atPath: res.path) else {
        note("内置资源目录为空: \(res.path)")
        return false
    }
    for name in names where !name.hasPrefix(".") {
        let src = res.appendingPathComponent(name)
        let dst = WORK.appendingPathComponent(name)

        let srcData = try? Data(contentsOf: src)
        let dstData = try? Data(contentsOf: dst)

        // 内容一致就不动，避免无谓地改 mtime
        if let s = srcData, let d = dstData, s == d { continue }

        // 目标已存在且内容不同 → 说明用户改过，先留一份 .bak，绝不静默覆盖
        if dstData != nil {
            let bak = WORK.appendingPathComponent(name + ".bak")
            try? fm.removeItem(at: bak)
            try? fm.copyItem(at: dst, to: bak)
        }

        try? fm.removeItem(at: dst)
        do {
            try fm.copyItem(at: src, to: dst)
        } catch {
            note("复制 \(name) 失败: \(error.localizedDescription)")
        }
    }
    // 可执行权限（.command 需要）
    let cmd = WORK.appendingPathComponent("启动面板.command").path
    try? fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: cmd)
    return true
}

// MARK: - Python 解释器

func findPython() -> String {
    let venv = WORK.appendingPathComponent(".venv/bin/python").path
    if fm.isExecutableFile(atPath: venv) { return venv }
    let candidates = [
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/usr/bin/python3",
    ]
    for p in candidates where fm.isExecutableFile(atPath: p) { return p }
    return "/usr/bin/python3"
}

// MARK: - 进程

func launch(_ script: String, extra: [String]) -> Process? {
    if !fm.fileExists(atPath: LOG.path) {
        fm.createFile(atPath: LOG.path, contents: nil)
    }
    let p = Process()
    p.executableURL = URL(fileURLWithPath: findPython())
    p.arguments = ["bootstrap.py", script] + extra
    p.currentDirectoryURL = WORK

    if let fh = try? FileHandle(forWritingTo: LOG) {
        fh.seekToEndOfFile()
        p.standardOutput = fh
        p.standardError = fh
    }
    do {
        try p.run()
        return p
    } catch {
        note("启动 \(script) 失败: \(error.localizedDescription)")
        return nil
    }
}

func stopProcess(_ p: Process?) {
    guard let p = p, p.isRunning else { return }
    p.terminate()
    for _ in 0..<60 {
        if !p.isRunning { break }
        usleep(100_000)  // 0.1s × 60 = 最多等 6 秒
    }
    if p.isRunning {
        kill(p.processIdentifier, SIGKILL)
    }
}

/// 通过端口找外部占用的进程并杀掉（用于"不是本应用启动"的情况）
func killByPort(_ port: Int) {
    let sh = Process()
    sh.executableURL = URL(fileURLWithPath: "/bin/sh")
    sh.arguments = ["-c", "lsof -nP -iTCP:\(port) -sTCP:LISTEN -t 2>/dev/null | xargs -r kill"]
    try? sh.run()
    sh.waitUntilExit()
}

// MARK: - 状态探测（绕开系统代理，直连 127.0.0.1）

func probe(path: String, port: Int, timeout: TimeInterval = 1.5) -> [String: Any]? {
    guard let url = URL(string: "http://127.0.0.1:\(port)\(path)") else { return nil }
    let cfg = URLSessionConfiguration.ephemeral
    cfg.connectionProxyDictionary = [:]
    cfg.timeoutIntervalForRequest = timeout
    cfg.requestCachePolicy = .reloadIgnoringLocalCacheData
    let session = URLSession(configuration: cfg)

    var result: [String: Any]?
    let sem = DispatchSemaphore(value: 0)
    var req = URLRequest(url: url)
    req.timeoutInterval = timeout
    session.dataTask(with: req) { data, _, _ in
        if let d = data {
            result = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any]
        }
        sem.signal()
    }.resume()
    _ = sem.wait(timeout: .now() + timeout + 1)
    return result
}

func serviceRunning() -> Bool { probe(path: "/health", port: CONVERTER_PORT) != nil }

// MARK: - 应用

final class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var menu: NSMenu!

    var converter: Process?
    var panel: Process?

    var statusTitle: NSMenuItem!
    var accountItem: NSMenuItem!
    var toggleItem: NSMenuItem!
    var panelItem: NSMenuItem!
    var loginItem: NSMenuItem!

    var health: [String: Any]?
    var busy = false
    var timer: Timer?

    func applicationDidFinishLaunching(_ n: Notification) {
        setupStatusItem()
        buildMenu()
        syncScripts()
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            self?.refresh()
        }
        RunLoop.main.add(timer!, forMode: .common)
    }

    func applicationWillTerminate(_ n: Notification) {
        stopProcess(converter)
        stopProcess(panel)
    }

    // MARK: 图标

    func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.image = icon(running: false)
            button.imagePosition = .imageOnly
            button.toolTip = "wb-openai 网关"
        }
    }

    func icon(running: Bool) -> NSImage? {
        let name = running ? "bolt.horizontal.circle.fill" : "bolt.horizontal.circle"
        let img = NSImage(systemSymbolName: name, accessibilityDescription: running ? "运行中" : "已停止")
        img?.isTemplate = true
        return img
    }

    // MARK: 菜单

    func buildMenu() {
        menu = NSMenu()
        menu.autoenablesItems = false

        statusTitle = NSMenuItem(title: "检测中…", action: nil, keyEquivalent: "")
        statusTitle.isEnabled = false
        menu.addItem(statusTitle)

        accountItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
        accountItem.isEnabled = false
        menu.addItem(accountItem)

        menu.addItem(.separator())

        toggleItem = NSMenuItem(title: "开启服务", action: #selector(toggleService), keyEquivalent: "s")
        toggleItem.target = self
        menu.addItem(toggleItem)

        panelItem = NSMenuItem(title: "打开 Web 面板", action: #selector(openPanel), keyEquivalent: "w")
        panelItem.target = self
        menu.addItem(panelItem)

        let copyItem = NSMenuItem(title: "复制 API Base", action: #selector(copyBase), keyEquivalent: "c")
        copyItem.target = self
        menu.addItem(copyItem)

        menu.addItem(.separator())

        let logItem = NSMenuItem(title: "查看日志", action: #selector(openLog), keyEquivalent: "l")
        logItem.target = self
        menu.addItem(logItem)

        let dirItem = NSMenuItem(title: "打开数据目录", action: #selector(openDir), keyEquivalent: "")
        dirItem.target = self
        menu.addItem(dirItem)

        loginItem = NSMenuItem(title: "开机自启", action: #selector(toggleLogin), keyEquivalent: "")
        loginItem.target = self
        menu.addItem(loginItem)

        menu.addItem(.separator())

        let quit = NSMenuItem(title: "退出（并停止服务）", action: #selector(quit), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)

        statusItem.menu = menu
        refreshLoginState()
    }

    // MARK: 刷新

    func refresh() {
        let h = probe(path: "/health", port: CONVERTER_PORT)
        health = h
        let running = (h != nil)

        statusItem.button?.image = icon(running: running)
        statusItem.button?.toolTip = running ? "wb-openai 网关 · 运行中" : "wb-openai 网关 · 已停止"

        if let h = h {
            let model = (h["default_model"] as? String) ?? "-"
            if converter?.isRunning == true {
                statusTitle.title = "● 运行中 · \(model)"
            } else {
                statusTitle.title = "● 运行中（非本应用启动）· \(model)"
            }
            toggleItem.title = "关闭服务"
            toggleItem.isEnabled = !busy
            accountItem.title = accountText(h)
            accountItem.isHidden = false
        } else {
            statusTitle.title = "○ 已停止"
            toggleItem.title = busy ? "启动中…" : "开启服务"
            toggleItem.isEnabled = !busy
            accountItem.isHidden = true
        }

        // 面板项：面板在跑就显示"打开"，否则"启动并打开"
        let panelUp = probe(path: "/api/status", port: PANEL_PORT) != nil
        panelItem.title = panelUp ? "打开 Web 面板" : "启动 Web 面板"
    }

    func accountText(_ h: [String: Any]) -> String {
        guard let cred = h["credential"] as? [String: Any] else {
            if let err = h["credential_error"] as? String {
                return "账号：\(String(err.prefix(40)))"
            }
            return "账号：未登录"
        }
        let nick = (cred["nickname"] as? String) ?? "-"
        let expired = (cred["token_expired"] as? Bool) ?? false
        if expired { return "账号：\(nick) · token 已过期" }
        let exp = (cred["token_expires_at"] as? Double) ?? 0
        let days = Int((exp / 1000 - Date().timeIntervalSince1970) / 86400)
        return days > 0 ? "账号：\(nick) · token 剩 \(days) 天" : "账号：\(nick) · token 即将过期"
    }

    // MARK: 动作

    @objc func toggleService() {
        if busy { return }
        busy = true
        refresh()

        if health != nil {
            // 关闭
            DispatchQueue.global().async {
                stopProcess(self.converter)
                self.converter = nil
                // 等端口释放；若是外部进程，按端口清理
                var released = false
                for _ in 0..<30 {
                    if !serviceRunning() { released = true; break }
                    usleep(200_000)
                }
                if !released {
                    killByPort(CONVERTER_PORT)
                    usleep(500_000)
                }
                DispatchQueue.main.async { self.busy = false; self.refresh() }
            }
        } else {
            // 开启
            syncScripts()
            DispatchQueue.main.async {
                self.converter = launch("converter.py",
                                        extra: ["--port", "\(CONVERTER_PORT)",
                                                "--log", LOG.path])
                let proc = self.converter
                // 首启动可能要建 venv 装依赖（实测约 1~2 分钟），给足时间；
                // 但进程若提前退出就立刻报错，不干等。
                DispatchQueue.global().async {
                    var up = false
                    var died = false
                    for _ in 0..<600 {                  // 上限 600 × 0.5s = 300s
                        if serviceRunning() { up = true; break }
                        if let p = proc, !p.isRunning { died = true; break }
                        usleep(500_000)
                    }
                    DispatchQueue.main.async {
                        self.busy = false
                        self.refresh()
                        if !up {
                            let tail = self.logTail(6)
                            self.showError(died ? "服务启动后立即退出" : "服务启动超时",
                                           info: tail.isEmpty
                                                 ? "日志：\(LOG.path)"
                                                 : "日志尾部：\n\n\(tail)")
                        }
                    }
                }
            }
        }
    }

    /// 取日志最后几行，用于报错时直接展示原因
    func logTail(_ n: Int) -> String {
        guard let t = try? String(contentsOfFile: LOG.path, encoding: .utf8) else { return "" }
        return t.split(separator: "\n", omittingEmptySubsequences: true)
            .suffix(n).joined(separator: "\n")
    }

    @objc func openPanel() {
        if probe(path: "/api/status", port: PANEL_PORT) == nil {
            syncScripts()
            panel = launch("wb_panel.py", extra: ["--no-browser"])
            // 给面板一点启动时间
            DispatchQueue.global().async {
                for _ in 0..<40 {
                    if probe(path: "/api/status", port: PANEL_PORT) != nil { break }
                    usleep(500_000)
                }
                DispatchQueue.main.async {
                    NSWorkspace.shared.open(URL(string: "http://127.0.0.1:\(PANEL_PORT)")!)
                    self.refresh()
                }
            }
        } else {
            NSWorkspace.shared.open(URL(string: "http://127.0.0.1:\(PANEL_PORT)")!)
        }
    }

    @objc func copyBase() {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString("http://127.0.0.1:\(CONVERTER_PORT)/v1", forType: .string)
        flash("已复制 API Base")
    }

    @objc func openLog() {
        if fm.fileExists(atPath: LOG.path) {
            NSWorkspace.shared.open(LOG)
        } else {
            showError("还没有日志", info: "先开启一次服务。")
        }
    }

    @objc func openDir() {
        try? fm.createDirectory(at: WORK, withIntermediateDirectories: true)
        NSWorkspace.shared.open(WORK)
    }

    // MARK: 开机自启

    var launchAgentPath: String {
        HOME.appendingPathComponent("Library/LaunchAgents/cn.wbopenai.menubar.plist").path
    }

    func refreshLoginState() {
        loginItem.state = fm.fileExists(atPath: launchAgentPath) ? .on : .off
    }

    @objc func toggleLogin() {
        if fm.fileExists(atPath: launchAgentPath) {
            // 关闭
            let sh = Process()
            sh.executableURL = URL(fileURLWithPath: "/bin/sh")
            sh.arguments = ["-c", "launchctl bootout gui/$(id -u) '\(launchAgentPath)' >/dev/null 2>&1; rm -f '\(launchAgentPath)'"]
            try? sh.run()
            sh.waitUntilExit()
            refreshLoginState()
            flash("已关闭开机自启")
        } else {
            let exe = Bundle.main.executableURL?.path ?? ""
            let plist = """
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
            <plist version="1.0">
            <dict>
              <key>Label</key><string>cn.wbopenai.menubar</string>
              <key>ProgramArguments</key><array><string>\(exe)</string></array>
              <key>RunAtLoad</key><true/>
              <key>KeepAlive</key><false/>
            </dict>
            </plist>
            """
            try? fm.createDirectory(atPath: HOME.appendingPathComponent("Library/LaunchAgents").path,
                                    withIntermediateDirectories: true)
            try? plist.write(toFile: launchAgentPath, atomically: true, encoding: .utf8)
            let sh = Process()
            sh.executableURL = URL(fileURLWithPath: "/bin/sh")
            sh.arguments = ["-c", "launchctl bootstrap gui/$(id -u) '\(launchAgentPath)' 2>/dev/null || true"]
            try? sh.run()
            sh.waitUntilExit()
            refreshLoginState()
            flash("已开启开机自启")
        }
    }

    // MARK: 提示

    func flash(_ msg: String) {
        statusItem.button?.toolTip = msg
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
            let running = self.health != nil
            self.statusItem.button?.toolTip = running ? "wb-openai 网关 · 运行中" : "wb-openai 网关 · 已停止"
        }
    }

    func showError(_ title: String, info: String) {
        let a = NSAlert()
        a.messageText = title
        a.informativeText = info
        a.alertStyle = .warning
        a.addButton(withTitle: "好")
        a.runModal()
    }

    @objc func quit() {
        timer?.invalidate()
        stopProcess(converter)
        stopProcess(panel)
        NSApp.terminate(nil)
    }
}

// MARK: - 自检模式（不启动 UI，用于自动化验证）

func runSelfTest() -> Int32 {
    print("== wb-openai 菜单栏 App 自检 ==")
    print("工作目录: \(WORK.path)")
    print("Python  : \(findPython())")

    print("\n[1] 同步内置脚本")
    guard syncScripts() else { print("✗ 同步失败"); return 1 }
    let names = (try? fm.contentsOfDirectory(atPath: WORK.path)) ?? []
    let pyCount = names.filter { $0.hasSuffix(".py") }.count
    print("✓ 已同步，.py 文件 \(pyCount) 个")

    print("\n[2] 初始状态")
    if serviceRunning() {
        print("  端口已被占用，先清场（模拟外部残留进程）")
        killByPort(CONVERTER_PORT)
        for _ in 0..<20 {
            if !serviceRunning() { break }
            usleep(200_000)
        }
    }
    print("  服务运行中: \(serviceRunning())")

    print("\n[3] 启动转换器")
    let p = launch("converter.py", extra: ["--port", "\(CONVERTER_PORT)", "--log", LOG.path])
    guard let proc = p else { print("✗ 启动失败"); return 1 }
    print("  pid: \(proc.processIdentifier)")

    var up = false
    var died = false
    for i in 0..<600 {                      // 上限 300s（首启动要建 venv 装依赖）
        if serviceRunning() { up = true; print("  ✓ 第 \(i) 次探测就绪（约 \(Double(i) * 0.5)s）"); break }
        if !proc.isRunning { died = true; print("  ✗ 进程已退出（第 \(i) 次探测时）"); break }
        if i % 20 == 0 && i > 0 { print("  … 启动中 \(i / 2)s") }
        usleep(500_000)
    }
    guard up else {
        print("✗ 未就绪，日志尾部：")
        if let t = try? String(contentsOfFile: LOG.path, encoding: .utf8) {
            print(t.split(separator: "\n").suffix(12).joined(separator: "\n"))
        }
        return 1
    }
    _ = died

    print("\n[4] 读取 /health")
    if let h = probe(path: "/health", port: CONVERTER_PORT) {
        let cred = h["credential"] as? [String: Any]
        print("  responses_api: \(h["responses_api"] ?? "?")")
        print("  default_model: \(h["default_model"] ?? "?")")
        print("  账号: \(cred?["nickname"] ?? "-")")
    } else {
        print("  ✗ 读不到")
    }

    print("\n[5] 实际对话")
    let cfg = URLSessionConfiguration.ephemeral
    cfg.connectionProxyDictionary = [:]
    cfg.timeoutIntervalForRequest = 120
    let session = URLSession(configuration: cfg)
    var req = URLRequest(url: URL(string: "http://127.0.0.1:\(CONVERTER_PORT)/v1/chat/completions")!)
    req.httpMethod = "POST"
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    req.httpBody = """
    {"model":"glm-5.2","messages":[{"role":"user","content":"只回复两个字：在的"}]}
    """.data(using: .utf8)
    let sem = DispatchSemaphore(value: 0)
    var reply = "（无响应）"
    session.dataTask(with: req) { data, _, err in
        if let d = data,
           let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
           let ch = (j["choices"] as? [[String: Any]])?.first,
           let msg = ch["message"] as? [String: Any],
           let c = msg["content"] as? String {
            reply = c
        } else if let e = err {
            reply = "错误: \(e.localizedDescription)"
        }
        sem.signal()
    }.resume()
    _ = sem.wait(timeout: .now() + 130)
    print("  回复: \(reply.trimmingCharacters(in: .whitespacesAndNewlines))")

    print("\n[6] 停止转换器")
    stopProcess(proc)
    var down = false
    for _ in 0..<30 {
        if !serviceRunning() { down = true; break }
        usleep(200_000)
    }
    print(down ? "  ✓ 已停止，端口释放" : "  ✗ 仍在运行")

    let ok = up && down && !reply.contains("错误")
    print("\n== \(ok ? "自检通过 ✅" : "自检失败 ❌") ==")
    return ok ? 0 : 1
}

// MARK: - 入口

if CommandLine.arguments.contains("--selftest") {
    exit(runSelfTest())
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)   // 只在菜单栏，不进 Dock
app.run()
