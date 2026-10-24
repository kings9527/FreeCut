# FreeCut · 免费开源的桌面视频剪辑器

[![ci](https://github.com/kings9527/FreeCut/actions/workflows/ci.yml/badge.svg)](https://github.com/kings9527/FreeCut/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

纯 Python + FFmpeg 的离线视频剪辑器（Qt 界面），可读写剪映草稿，也能导出
mp4 / m4a / GIF、FCP7 XML 和 `.prproj`。不登录、不联网、不上传任何素材。

## 这是什么

这是对**剪映专业版（JianyingPro）的功能复刻**。

数据模型、草稿格式、调色参数与量纲、转场与特效的行为，全部来自对本机安装的
剪映专业版做逆向分析：草稿 JSON 结构实测、DLL 字符串扫描、着色器与 Lua 脚本对照、
逐参数验证单位。不是"参考了剪映的交互"，而是**照着它的实现把它重新做了一遍**。

- 与剪映（字节跳动）、Adobe Premiere 均无关联，非官方版本
- 仓库内不包含任何第三方软件本体、素材包、字体或 LUT——所有效果都是自己写的
- 做不到的部分写在「明确的边界」一节：云端能力不实现，也不绕过会员

开发体量：累计约 **45 亿 tokens** 的模型交互，收敛到 1084 条测试、56 个模块、约 1.9 万行代码。

## 功能

| 模块 | 能力 |
|---|---|
| 剪辑 | 多轨、切割/删除/波纹、拖动、吸附、框选多选、Ctrl+0 适应窗口、关键帧 |
| 转场 | 37 个引擎（叠化/闪黑闪白/各类擦除/滑动/圆形/百叶/时钟/缩放…），时长可调，时间线上以"覆盖接缝"的块显示，可一键应用到所有接缝 |
| 文字 | 内容、字体（读本机字体）、字号、颜色、粗体/斜体/下划线、行距/字距、对齐/竖排、描边（宽·色·透明度）、阴影（强度·色·距离·角度）、背景框（色·透明度·圆角）、逐字高亮、文字/整体透明度、**朗读成音频**（Windows SAPI，离线） |
| 音频 | 音量、淡入淡出、降噪、变声、三段均衡 EQ、响度统一（EBU R128） |
| 调色 | 亮度/对比/饱和/色温/色调/高光/阴影/白场/黑场/锐化/暗角、HSL（8 色带，色圈 + 渐变滑杆）、RGB/单通道曲线、色轮、蒙版、调节图层、本地 LUT（可导入 `.cube`） |
| 特效 | 17 种程序化特效（模糊/马赛克/镜像/色散/抖动/故障/呼吸/脉冲/闪白/星光/CRT/老电影/光晕/漏光/双色调/磨皮/抠像近似），可叠链、可在属性页调参 |
| 导出 | mp4（H.264/HEVC，可用硬件编码）、m4a 仅音频、GIF、剪映草稿、FCP7 XML、`.prproj`、静帧；多路同时导出、导出后自动检测（黑帧/静音/帧数/音画同步） |
| 一键成片 | 图文成片（本机 SAPI 配音 + 分句字幕）、自动字幕（离线 Whisper，可选） |
| 健壮性 | 素材缺失校验与重新链接、自动保存、崩溃恢复、未保存提示、最近打开 |

## 明确的边界（不做，也不假装做）

- 云端能力：贴纸、模板、数字人、智能包装、AI 音色、AI 超清、AI 补帧 —— 需要账号与云端模型，本项目不实现、不绕过会员。
- 剪映资源包里的特效/动画本体（着色器与素材）不还原；本项目用程序化实现近似效果。
- 花字（剪映的 text style 资源）导入未实现；内置 20 组自研文字样式可用。
- 性能：CPU 渲染，1080p 4 层约 340 ms/帧（预览建议用 1/3 画布档）。

## 安装与运行

需要 **Python 3.11+** 与 **FFmpeg**（建议 6.x）。

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt

# 让程序找到 ffmpeg：装进 PATH，或指定路径
set FREECUT_FFMPEG=C:\path\to\ffmpeg.exe

python -m freecut gui
```

命令行入口还有：`python -m freecut --help`（导入/导出草稿、导出视频、评测码率、探测硬件等）。

### 打包成 exe

```bash
python -m PyInstaller FreeCutDir.spec --noconfirm
dist\FreeCutDir\FreeCutDir.exe gui
```

## 测试

```bash
set QT_QPA_PLATFORM=offscreen
set FREECUT_NO_AUDIO=1
python -m unittest discover -s tests -p "test_*.py"
```

- 需要 FFmpeg 的用例会读 `FREECUT_FFMPEG`，找不到时自动跳过。
- 部分用例依赖 `out/chroma_src.png` 这类测试素材（未入库），同样会跳过。

## 目录结构

```
freecut/          核心：模型、渲染、调色、音频、特效、草稿互操作、Qt 界面
tests/            单元/端到端测试（unittest）
tools/            开发脚本（基准测试、文档核对等）
examples/         小型示例脚本
```

## 许可

代码以 **MIT** 发布，见 [LICENSE](LICENSE)。
第三方依赖与运行时的许可见 [NOTICE](NOTICE)。

## 贡献

欢迎 issue / PR。提交前请：

1. `python -m unittest discover -s tests`（或至少跑你改到的模块）
2. 新增行为配一条可复现的测试
3. 不要提交素材、字体、第三方二进制

---

> 我已经尽力了，下一章《向北》。
