# Ivyea Translate

桌面 AI 翻译软件（Windows 优先）：划词翻译 · 截图翻译 · 反向写作。轻量、简洁、好用。
**内置免费翻译引擎（DeepL / Google / 必应 自动回退），装完即用，无需任何配置**；
配置你自己的大模型（任意 OpenAI 兼容接口）可解锁风格控制（美式 / 英式 / 正式 / 口语 / 学术 / 简洁）、邮件改写与更高翻译质量。

---

## 交流与反馈

欢迎扫码加入微信群，反馈 Bug、交流 Ivyea Translate 使用经验、AI 工具与亚马逊运营相关知识。**也欢迎提改进建议**——功能需求、交互优化、文档纠错都行，可在群里直接说，或到 GitHub 提 [Issue](https://github.com/Hector-xue/ivyea-translate/issues) / PR。群二维码可能会过期；如果扫码失效，可先关注公众号，再获取最新群二维码。

<table>
  <tr>
    <td align="center" width="50%">
      <img src="docs/assets/wechat-group-qr.png" alt="Ivyea 微信交流群二维码" width="300" />
      <br />
      <strong>微信群：Ivyea 的精神股东们</strong>
      <br />
      <sub>反馈 Bug / 交流 AI 与运营 / 提改进建议</sub>
    </td>
    <td align="center" width="50%">
      <img src="docs/assets/wechat-official-account-qr.jpg" alt="Ivyea 公众号二维码" width="220" />
      <br />
      <strong>公众号</strong>
      <br />
      <sub>群二维码失效时，关注后获取最新版</sub>
    </td>
  </tr>
</table>

---

## 功能

| 功能 | 触发方式 | 行为 |
| --- | --- | --- |
| 划词翻译 | 选中文字后按 **Ctrl+C+C**（连按两下 C） | 光标下方弹出译文弹窗（可拖动、可钉住、可调大小）；默认**智能方向**（中↔英自动）；弹窗内「详解」可展开重点词读音/语法/地道说法（需大模型） |
| 截图翻译 | `Ctrl+Alt+S` 框选区域 | 本地 OCR 识别 → 翻译，弹窗定位在框选区域**外侧**，不覆盖原文；弹窗内可查看识别原文 |
| 主窗口 | 点击托盘/任务栏图标 | 手动翻译 + 反向写作（用母语写→出地道外语+回译校对，含邮件/消息/评论/社媒场景）+ 历史 + 设置 |

截图翻译快捷键可在「设置」里改，并可单独设定目标语言。

### 外观主题

「设置 → 外观主题」可在六套皮肤间即点即换，每套都有实拍背景照片和对应的动效：

| 主题 | 画面 | 动效 |
| --- | --- | --- |
| 常春藤 | 常春藤叶丛 | 藤蔓沿窗口边缘生长、长叶、随风轻摆 |
| 爱国风 | 天安门夜景 / 红灯笼 | 五星红旗丝绸飘动 + 金色微粒 |
| 星海 | 哈勃望远镜拍摄的星云 | 多层视差星野漂移 + 流星（深色） |
| 樱花 | 樱花实拍 | 花瓣飘落、自转、远近视差 |
| 赛博 | 城市夜景霓虹 | 霓虹辉光呼吸 + 扫描光带（深色） |
| 雪山 | 雪山晨雾 | 云雾分层横向缓移 |

- **启用动效**：关掉后只保留静态背景照片，完全不占 CPU；窗口隐藏到托盘时动效本来也会自动停。
- **显示主题横幅**：顶部那条主题横幅可一键收起，小窗口下把空间全留给翻译区。
- 所有背景与素材都是**公有领域 / CC0 的真实照片**，逐张来源见 [`assets/themes/CREDITS.md`](assets/themes/CREDITS.md)。

## 下载安装（推荐）

官网直接下载：**https://translate.ivyea.com**（或到 [Releases](../../releases)）：

- **IvyeaTranslate-Setup.exe** —— 安装版：向导安装、开始菜单+桌面快捷方式、可卸载（装到当前用户目录，无需管理员）
- **IvyeaTranslate-mac-arm64.dmg** —— macOS（Apple Silicon）**Beta**：拖进「应用程序」即可，未签名，见下方 macOS 说明

注意：
- 全局快捷键用系统原生 RegisterHotKey 注册，若与其他软件冲突，设置页「状态」会红字提示具体哪条失败，改个组合键保存即可
- 对以管理员权限运行的窗口取词/热键无效（Windows 安全机制），需要的话以管理员身份运行本软件
- 排查问题看日志：`%USERPROFILE%\.ivyea-translate\app.log`

## macOS（Beta，未签名）

只在 CI 上验证过"能构建、能启动"，交互功能尚未在真机逐项验证，**当 Beta 用**。

- 产物：`IvyeaTranslate-mac-arm64.dmg`（Apple Silicon；Intel 机型未提供）
- 未做签名与公证，首次打开会被 Gatekeeper 拦（"无法验证开发者"）：**右键 → 打开**，或
  `xattr -dr com.apple.quarantine /Applications/IvyeaTranslate.app`
- 权限：全局快捷键需「系统设置 → 隐私与安全性 → 辅助功能」授权；截图翻译需「屏幕录制」授权
- 划词是 **⌘+C+C**（复制键随平台变，界面文案会自动切换）
- 应用内自动更新仅 Windows 安装版可用；macOS 上发现新版会引导到官网下载新 dmg

### macOS 人工验证清单（待真机核验）

- [ ] dmg 能挂载，拖进「应用程序」后可启动（右键→打开绕过 Gatekeeper）
- [ ] 托盘（菜单栏）图标出现，点击能开主窗口
- [ ] 授权「辅助功能」后 `⌃⌥S` 能唤起截图框选
- [ ] 授权「屏幕录制」后截图 OCR 能识别出文字（否则截到的是黑屏/桌面壁纸）
- [ ] 选中文字连按两次 ⌘+C 能弹出译文弹窗
- [ ] Retina 屏上弹窗定位不偏移
- [ ] 主窗口四个页签排版正常（含新的「清空」按钮与回译校对框高度）

## 自动更新

安装版应用启动后会静默检查更新（源：`https://translate.ivyea.com/download/version.json`），
发现新版会弹窗提示，点「立即更新」即自动下载（带进度）→ 静默安装 → 自动重启，全程无需去官网；设置页「关于与更新」也留了入口。
非安装版（macOS / 源码运行）会引导到官网下载新版。可在配置里关闭 `update.auto_check`。

发版流程（维护者）：推 `v*` tag → GitHub Actions 云端构建并挂 Release →
服务器 cron（每 20 分钟，`deploy/sync-release.sh`）自动把 exe 同步到官网下载目录并刷新 version.json。

## 源码运行（Windows，Python 3.9+）

```bat
git clone <repo> ivyea-translate
cd ivyea-translate
pip install -r requirements.txt
python -m ivyea_translate
```

开箱即用：默认「自动」引擎——没配大模型就用内置免费翻译（DeepL / Google / 必应 三端点按质量排序自动回退，DeepL 限流时无缝降级），装完即可划词/截图翻译。

想要更高质量与风格/邮件助手：打开「设置 → 翻译模型」→ 选服务商预设（DeepSeek / OpenAI / OpenRouter / 硅基流动 / 自定义）→ 填 API Key →「测试连接」→ 保存。引擎选择可设为 自动 / 免费 / 我的大模型。

配置文件在 `~/.ivyea-translate/config.json`，历史在 `history.json`。

## 打包成 exe（在 Windows 机器上）

```bat
build.bat            :: 文件夹版（启动快）  -> dist\IvyeaTranslate\IvyeaTranslate.exe
```

安装包：装 [Inno Setup 6](https://jrsoftware.org/isinfo.php) 后在文件夹版基础上 `iscc installer.iss` → `dist\IvyeaTranslate-Setup.exe`。
CI 会在推 `v*` tag 时自动构建 Windows 安装包并挂到 Release。

macOS（在 Mac 上）：`pyinstaller ivyea-translate-mac.spec` → `dist/IvyeaTranslate.app`；
CI 的 `build-macos` job 会再用 `hdiutil` 打成 dmg 并挂到 Release。

## 开发与测试

逻辑层（配置 / prompt 编译 / OCR 段落合并 / 弹窗定位 / 剪贴板过滤）全部纯函数化，Linux 无显示环境也能跑：

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/ -q
```

Linux 开发机装 pynput 需 `pip install --no-deps pynput`（跳过 evdev 编译；热键功能仅 Windows 上验证）。

## Windows 人工验证清单

- [ ] 启动后托盘出现「译」图标；重复启动第二个实例自动退出
- [ ] 全新安装（未填 API Key）：选中文字后 Ctrl+C+C 即翻译（走内置免费引擎）
- [ ] 记事本里选中一段文字按 `Ctrl+C+C`：光标下方弹窗流式出译文
- [ ] `Ctrl+Alt+S` 框选一段文字截图：识别并翻译，弹窗在框选区域外侧，不遮挡原文
- [ ] 弹窗可拖动、可调大小；Esc 关闭；📌 钉住后可同时开多个
- [ ] 150% 缩放显示器上截图翻译坐标不偏移（DPI 验证）
- [ ] 设置页填 API Key + 测试连接；引擎选"我的大模型"后流式翻译、风格生效
- [ ] 邮件页（需大模型）：草稿改写为地道邮件并生成主题

## 架构

```
ivyea_translate/
├── app.py               # 装配：单实例、托盘、双击复制/截图链路接线
├── config.py            # 配置（引擎/预设/语言/风格/快捷键）
├── llm.py               # OpenAI 兼容流式客户端
├── free_engine.py       # 免费引擎（DeepL/Google/必应 回退，纯逻辑可测）
├── translator.py        # prompt 编译（纯函数）+ 翻译线程
├── ocr.py               # RapidOCR + 行框合并段落（纯函数）
├── hotkeys.py           # 截图全局热键（RegisterHotKey → Qt 信号）
├── clipboard_watch.py   # Ctrl+C+C 触发划词（is_double_copy 纯函数）
└── ui/
    ├── theme.py         # 设计令牌与 QSS
    ├── main_window.py   # 翻译 / 邮件 / 历史 / 设置
    ├── popup.py         # 可拖动可调大小结果弹窗 + 智能定位（纯函数）
    └── capture_overlay.py # 截图框选层（DPI 换算集中于此）
```
