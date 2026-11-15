// 小朋友 · 语音助手 —— 一个壳：起本地服务 + 用 WKWebView 显示页面
// 2026-11-15 建。目的：双击出独立窗口，不碰浏览器的标签和权限设置。

import Cocoa
import WebKit
import AVFoundation

let PORT = 3930
let ROOT = ProcessInfo.processInfo.environment["VA_ROOT"]
    ?? "/Users/jieda/Documents/deepseek-harness/default-workspace/voice-assistant"

// 壳自己的日志：页面崩了、麦克风授没授权，都写在这
func appLog(_ msg: String) {
    let line = "[\(Date())] \(msg)\n"
    if let fh = FileHandle(forWritingAtPath: "/tmp/lc-app.log") {
        fh.seekToEndOfFile()
        fh.write(line.data(using: .utf8)!)
        fh.closeFile()
    } else {
        try? line.data(using: .utf8)?.write(to: URL(fileURLWithPath: "/tmp/lc-app.log"))
    }
}

func portOpen() -> Bool {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/nc")
    p.arguments = ["-z", "-w", "1", "127.0.0.1", "\(PORT)"]
    p.standardOutput = FileHandle.nullDevice
    p.standardError = FileHandle.nullDevice
    do { try p.run() } catch { return false }
    p.waitUntilExit()
    return p.terminationStatus == 0
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var server: Process?

    // ── 起服务 ──
    func startServer() {
        guard FileManager.default.fileExists(atPath: ROOT + "/start.sh") else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = ["-c", "exec ./start.sh"]
        p.currentDirectoryURL = URL(fileURLWithPath: ROOT)
        if let fh = FileHandle(forWritingAtPath: "/tmp/voice-assistant.out") {
            p.standardOutput = fh
            p.standardError = fh
        } else {
            p.standardOutput = FileHandle.nullDevice
            p.standardError = FileHandle.nullDevice
        }
        try? p.run()
        server = p
    }

    func waitForServer(_ tries: Int = 0) {
        if portOpen() {
            loadPage()
            return
        }
        if tries == 0 { startServer() }
        if tries > 40 {   // 20 秒还没起来
            webView.loadHTMLString("""
            <html><body style="background:#121218;color:#e9e9ef;font:18px -apple-system;padding:40px">
            <h2>本地服务没起来</h2>
            <p>看一下这个日志：<code>/tmp/voice-assistant.out</code></p>
            </body></html>
            """, baseURL: nil)
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.waitForServer(tries + 1)
        }
    }

    func loadPage() {
        webView.load(URLRequest(url: URL(string: "http://127.0.0.1:\(PORT)/?app=1")!))
    }

    // ── 界面 ──
    func applicationDidFinishLaunching(_ n: Notification) {
        buildMenu()

        // 麦克风权限由 app 自己问，别指望 WebView 里那套
        AVCaptureDevice.requestAccess(for: .audio) { granted in
            appLog("mic-access \(granted ? "allowed" : "denied")")
        }
        appLog("launch")

        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .default()
        // 有些系统版本要显式打开媒体采集（私有开关，存在才调，不存在就算了）
        let sel = Selector(("_setMediaCaptureEnabled:"))
        if cfg.responds(to: sel) {
            typealias Fn = @convention(c) (AnyObject, Selector, Bool) -> Void
            let imp = cfg.method(for: sel)
            unsafeBitCast(imp, to: Fn.self)(cfg, sel, true)
        }
        cfg.preferences.setValue(true, forKey: "developerExtrasEnabled")

        webView = WKWebView(frame: .zero, configuration: cfg)
        webView.uiDelegate = self
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")   // 别白闪

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1180, height: 820),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        window.title = "Local Chatter"
        window.backgroundColor = NSColor(calibratedRed: 0.07, green: 0.07, blue: 0.09, alpha: 1)
        window.contentView = webView
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        waitForServer()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { true }

    @objc func reload() { loadPage() }

    func buildMenu() {
        let mainMenu = NSMenu()
        let appItem = NSMenuItem()
        mainMenu.addItem(appItem)
        let m = NSMenu()
        m.addItem(withTitle: "关于 Local Chatter", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        m.addItem(NSMenuItem.separator())
        let r = NSMenuItem(title: "重新加载", action: #selector(reload), keyEquivalent: "r")
        r.target = self
        m.addItem(r)
        m.addItem(NSMenuItem.separator())
        m.addItem(withTitle: "退出", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = m
        NSApp.mainMenu = mainMenu
    }

    // 麦克风：让 WebView 直接放行，系统那一层仍会问一次
    @available(macOS 12.0, *)
    func webView(_ webView: WKWebView,
                 requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo,
                 type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(.grant)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        appLog("webcontent-terminated")   // 页面进程挂了（这就是他看到的「崩溃」）
        webView.reload()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        appLog("page-loaded")
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        appLog("nav-failed \(error.localizedDescription)")
        // ★ 绝对不能在这里再 load 一次。
        // 2026-11-15 踩过：导航被取消（Cancelled）也算失败 → 又发起新导航 →
        // 两个导航互相取消 → 页面无限重载，看起来就是「一说话就崩」。
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
