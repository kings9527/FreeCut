# FreeCut

**剪映专业版的开源复刻 —— Windows 桌面、纯 Python、离线跑。**

[![ci](https://github.com/kings9527/FreeCut/actions/workflows/ci.yml/badge.svg)](https://github.com/kings9527/FreeCut/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-green?style=flat)](LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-blue?style=flat)](https://www.python.org/)

装在自己机器上逆向出来的：草稿格式、参数单位、转场和特效的行为，一个参数一个参数地对。
顺手烧掉大约 **45 亿 tokens**，换来 1084 条测试和一套能用的剪辑器。

## 能干什么

- 多轨剪辑：切割、删除、波纹、拖动、吸附、框选、关键帧
- 37 种转场，时间线上以「覆盖接缝」的块显示，可一键套到所有接缝
- 文字：字体、字号、颜色、粗斜体、下划线、行距、字距、对齐、竖排、描边、阴影、背景框、逐字高亮、朗读成音频
- 音频：音量、淡入淡出、降噪、变声、三段 EQ、响度统一
- 调色：基础 11 项 + HSL 8 色带 + 曲线 + 色轮 + 蒙版 + 调节图层 + 本地 LUT
- 17 种程序化特效，能叠链、能调参
- 导出 mp4 / m4a / GIF / 剪映草稿 / FCP7 XML / prproj，支持多路同时导出和导出后检测
- 图文成片、自动字幕（离线 Whisper）

## 不做的

- 云端那套：贴纸、模板、数字人、AI 音色、AI 超清、AI 补帧 —— 不实现，也不绕会员
- 资源包里的特效/动画本体不还原，用程序化方式做近似
- 花字导入没做，内置 20 组自研样式
- CPU 渲染，1080p 四层约 340 ms/帧，预览建议用 1/3 画布

## 跑起来

需要 Python 3.11+ 和 FFmpeg。

```bash
pip install -r requirements.txt
set FREECUT_FFMPEG=C:\path\to\ffmpeg.exe
python -m freecut gui
```

打包成 exe：

```bash
python -m PyInstaller FreeCutDir.spec --noconfirm
dist\FreeCutDir\FreeCutDir.exe gui
```

## 测试

```bash
set QT_QPA_PLATFORM=offscreen
python -m unittest discover -s tests -p "test_*.py"
```

## 贡献

issue 和 PR 都欢迎。改行为请顺手带一条能复现的测试，别提交素材、字体和第三方二进制。

## License

[MIT](LICENSE) · 第三方依赖见 [NOTICE](NOTICE)

---

> 我已经尽力了，下一章《向北》。
