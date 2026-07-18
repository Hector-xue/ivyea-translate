# Ivyea Translate

桌面 AI 翻译软件（Windows 优先）：划词翻译 · 复制翻译 · 截图翻译 · 主窗口翻译。
翻译引擎用你自己配置的大模型（任意 OpenAI 兼容接口），支持多目标语言与风格（美式英语 / 英式英语 / 正式 / 口语 / 学术 / 简洁）。

## 功能

| 功能 | 触发方式 | 行为 |
| --- | --- | --- |
| 划词翻译 | 选中文字后按 `Ctrl+Alt+T` | 光标下方弹出译文弹窗（可拖动、可钉住） |
| 复制翻译 | 托盘/设置里开启后，复制任意文本 | 自动弹窗翻译（自家复制的译文不会触发） |
| 截图翻译 | `Ctrl+Alt+S` 框选区域 | 本地 OCR 识别 → 翻译，弹窗定位在框选区域**外侧**，不覆盖原文；弹窗内可查看识别原文 |
| 主窗口 | `Ctrl+Alt+I` 或托盘图标 | 手动输入翻译 + 历史记录 + 设置 |

快捷键都可在「设置」里改（pynput 语法，如 `<ctrl>+<alt>+t`）。

## 安装运行（Windows，Python 3.9+）

```bat
git clone <repo> ivyea-translate
cd ivyea-translate
pip install -r requirements.txt
python -m ivyea_translate
```

首次使用：打开「设置」→ 选服务商预设（DeepSeek / OpenAI / OpenRouter / 硅基流动 / 自定义）→ 填 API Key → 「测试连接」→ 保存。

配置文件在 `~/.ivyea-translate/config.json`，历史在 `history.json`。

## 打包成 exe（在 Windows 机器上）

```bat
build.bat            :: 文件夹版（启动快）  -> dist\IvyeaTranslate\IvyeaTranslate.exe
build.bat portable   :: 单文件便携版        -> dist\IvyeaTranslate.exe
```

两种都是**免安装绿色版**，双击即用（RapidOCR 模型已随包收集）。单文件版首次启动要解压临时目录，慢几秒。
如需带安装向导/开始菜单快捷方式的 setup 安装包，可在文件夹版基础上用 Inno Setup 再包一层。

## 开发与测试

逻辑层（配置 / prompt 编译 / OCR 段落合并 / 弹窗定位 / 剪贴板过滤）全部纯函数化，Linux 无显示环境也能跑：

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/ -q
```

Linux 开发机装 pynput 需 `pip install --no-deps pynput`（跳过 evdev 编译；热键功能仅 Windows 上验证）。

## Windows 人工验证清单

- [ ] 启动后托盘出现珊瑚色「译」图标；重复启动第二个实例自动退出
- [ ] 记事本里选中一段文字按 `Ctrl+Alt+T`：弹窗出现在光标下方并流式出译文；用户剪贴板原内容不被破坏
- [ ] 未选中任何文字按 `Ctrl+Alt+T`：托盘气泡提示「没有取到选中文字」
- [ ] 开启复制翻译后 `Ctrl+C` 复制文本：自动弹窗；点弹窗「复制」拿译文不会再次触发翻译
- [ ] `Ctrl+Alt+S` 框选一段文字截图：识别并翻译，弹窗在框选区域下方（区域贴屏幕底部时弹在上方），不遮挡原文
- [ ] 弹窗可拖动；Esc 关闭；📌 钉住后可同时开多个
- [ ] 150% 缩放显示器上截图翻译坐标不偏移（DPI 验证）
- [ ] 设置页改快捷键保存后立即生效
- [ ] 目标语言英语 + 美式/英式风格：拼写风格符合（color / colour）

## 架构

```
ivyea_translate/
├── app.py               # 装配：单实例、托盘、三条链路接线
├── config.py            # 配置（预设/语言/风格/快捷键）
├── llm.py               # OpenAI 兼容流式客户端
├── translator.py        # prompt 编译（纯函数）+ 翻译线程
├── ocr.py               # RapidOCR + 行框合并段落（纯函数）
├── hotkeys.py           # 全局热键（pynput → Qt 信号）
├── selection.py         # 取选中文字（备份剪贴板→Ctrl+C→还原，Win32 序号防竞态）
├── clipboard_watch.py   # 复制翻译（过滤规则纯函数）
└── ui/
    ├── theme.py         # 粉彩渐变 + 玻璃卡设计令牌与 QSS
    ├── main_window.py   # 翻译 / 历史 / 设置
    ├── popup.py         # 可拖动结果弹窗 + 智能定位（纯函数）
    └── capture_overlay.py # 截图框选层（DPI 换算集中于此）
```
