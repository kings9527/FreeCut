"""转场引擎的"真的还原了"检查：每个引擎在 u=0.5 都产出一张合法掩码，
且不同引擎的掩码不能全都长一个样（否则等于没实现）。
"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestTransitionEngines(unittest.TestCase):
    def test_every_engine_makes_a_valid_mask(self):
        import numpy as np

        from freecut.render import TRANSITION_KINDS, _transition_mask

        for kind in sorted(set(TRANSITION_KINDS.values())):
            with self.subTest(kind=kind):
                mask = _transition_mask(kind, 0.5, 64, 48)
                # 约定：返回"可广播到 (H,W)"的掩码（(H,1)/(1,W)/(H,W) 都行），
                # 合成时 numpy 会广播；这里按可广播性检查，而不是死抠形状。
                arr = np.broadcast_to(np.asarray(mask, dtype=np.float32), (48, 64))
                full = np.asarray(mask)
                self.assertGreaterEqual(float(arr.min()), 0.0, kind)
                self.assertLessEqual(float(arr.max()), 1.0, kind)
                self.assertGreater(full.size, 0, kind)

    def test_engines_are_not_all_the_same(self):
        import numpy as np

        from freecut.render import TRANSITION_KINDS, _transition_mask

        seen = set()
        for kind in sorted(set(TRANSITION_KINDS.values())):
            arr = np.asarray(_transition_mask(kind, 0.5, 64, 48))
            seen.add(hash(arr.round(3).tobytes()))
        self.assertGreaterEqual(len(seen), 8,
                                f"只有 {len(seen)} 种不同掩码，引擎可能是同一个实现")


if __name__ == "__main__":
    unittest.main(verbosity=2)
