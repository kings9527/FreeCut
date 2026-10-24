# 贡献指南

## 环境

- Python 3.11+
- FFmpeg 6.x（设置 `FREECUT_FFMPEG` 指向可执行文件，或加进 PATH）
- 依赖：`pip install -r requirements.txt`

## 跑测试

```bash
set QT_QPA_PLATFORM=offscreen
set FREECUT_NO_AUDIO=1
python -m unittest discover -s tests -p "test_*.py"
```

只跑某个模块：

```bash
python -m unittest tests.test_inspector_layout -v
```

## 提交规范

1. **一个改动一个主题**，提交信息写清"改了什么、为什么"。
2. 新增或修复行为，**必须**带一条能复现的测试；测试要断言可观察结果
   （渲染像素、模型字段、输出文件），不要只断言"函数被调用"。
3. 不提交：媒体素材、字体、第三方二进制、个人绝对路径、日志文件。
4. UI 改动请在描述里附截图（打包版 `dist\FreeCutDir\FreeCutDir.exe gui`）。

## 代码风格

- 4 空格缩进，行宽约 100
- 不引入新的重型依赖，除非在 PR 里说明理由与替代方案
- 平台相关代码用 `sys.platform` 分支，并保证其他平台可降级运行

## 报告问题

请附：复现步骤、期望结果、实际结果、`python -m freecut --version`、
以及相关片段/素材的最小工程（可脱敏）。
