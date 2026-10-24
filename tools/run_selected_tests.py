"""按模块跑测试：只加载名字匹配的测试类（秒级），不跑全量。

    python tools\run_selected_tests.py                     # 默认：Qt / 用户路径 / 界面一致性
    python tools\run_selected_tests.py TransitionWorkflow  # 只跑转场那一类（约 8 秒）
    python tools\run_selected_tests.py Issue               # 四类"用户实测问题"
    python tools\run_selected_tests.py Timeline            # 时间线相关
    python tools\run_selected_tests.py --list              # 看有哪些类、各自属于哪个模块

全量（786 条，约 9 分钟）用：``python -m unittest discover -s tests``。
改一个模块就只跑那个类——这是**约定**：改完立刻能定位，不用等十分钟。
"""

from __future__ import annotations

import re
import sys
import time
import unittest

sys.path.insert(0, ".")

#: tests 目录下所有测试模块（自动发现 test_*.py，新增文件不用改这里）
def _modules() -> tuple:
    import glob
    import os

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = []
    for path in sorted(glob.glob(os.path.join(here, "tests", "test_*.py"))):
        found.append("tests." + os.path.splitext(os.path.basename(path))[0])
    return tuple(found) or ("tests.test_freecut",)


MODULES = _modules()


def discover_classes():
    """返回 [(模块, 类名, 测试条数)]，按模块、源文件顺序。"""
    found = []
    for module_name in MODULES:
        path = module_name.replace(".", "/") + ".py"
        try:
            source = open(path, encoding="utf-8").read()
        except OSError:
            continue
        for match in re.finditer(r"^class (Test\w+)", source, re.M):
            name = match.group(1)
            body = source[match.end():]
            next_class = re.search(r"^class ", body, re.M)
            body = body[:next_class.start()] if next_class else body
            count = len(re.findall(r"^\s*def test_", body, re.M))
            found.append((module_name, name, count))
    return found


def classes_matching(needles):
    everything = discover_classes()
    if not needles:
        needles = ["Qt", "UserPath", "UiParity", "MediaCache", "Issue", "Transition"]
    picked = [entry for entry in everything
              if any(needle in entry[1] or needle in entry[0] for needle in needles)]
    return picked


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--list":
        for module, name, count in discover_classes():
            print(f"{module:32s} {name:34s} {count:3d} 条")
        return 0
    picked = classes_matching(argv)
    if not picked:
        print("没有匹配的测试类（用 --list 看名字）")
        return 2
    total = sum(count for _m, _n, count in picked)
    print(f"跑 {len(picked)} 个类 {total} 条：{' '.join(n for _m, n, _c in picked)}")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for module, name, _count in picked:
        suite.addTests(loader.loadTestsFromName(f"{module}.{name}"))
    started = time.time()
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    print(f"用时 {time.time() - started:.1f}s")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
