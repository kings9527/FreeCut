"""Colour pipeline reconstructed from the shipped JianyingPro 11.3.0 assets.

Every function here is a NumPy transcription of a shader or Lua script that
ships inside the application, so the parameter *semantics* (and the actual
maths) match the original:

``Resources/DefaultAdjustBundle/contrast_v3/AmazingFeature/xshader/colorAdjust.frag``
    brightness / contrast / saturation / highlight / shadow / black&white /
    temperature-tint / fade / light-sensation, applied in that fixed order.
``.../contrast_v3/AmazingFeature/lua/SeekModeScript.lua``
    contrast slider -> sigmoid contrast parameter, ``pivot = 0.435``.
``.../temperature_tone_v3/AmazingFeature/lua/Lscript.lua``
    temperature slider -> CCT, hue-adaptive white-balance matrix.
``.../black_white/AmazingFeature/lua/Lscript.lua``
    white/black level control points (``MAX_RANGE = 0.5``).
``.../shadow_v1/AmazingFeature/lua/ImageBusinessSlider.lua``
    legacy per-effect shadow response curve.

All images are ``float32`` arrays of shape (H, W, 3) with values in [0, 1].
Alpha, when present, is passed through untouched.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence, Tuple

import numpy as np

#: Luminance weights used by the app's saturation matrix (colorAdjust.frag).
SATURATION_LUMINANCE = np.array([0.208540, 0.702086, 0.089374], dtype=np.float32)

#: Contrast pivot used by contrast_v3.
CONTRAST_PIVOT = 0.435


def _clip01(a: np.ndarray) -> np.ndarray:
    return np.clip(a, 0.0, 1.0, out=a) if a.flags.writeable else np.clip(a, 0.0, 1.0)


# --------------------------------------------------------------------------- #
# brightness                                    (colorAdjust.frag, u_enableBrightness)
# --------------------------------------------------------------------------- #
def brightness(img: np.ndarray, value: float) -> np.ndarray:
    """``value`` in [-1, 1]; 0 is identity.

    Shader:
        p > 0 : p = 1 + v*5
        p <=0 : p = 1 / (1 - v*2.5),  rgb -= -v*0.01
        rgb = 1 - (1 - rgb) ** p
    """
    v = float(np.clip(value, -1.0, 1.0))
    if v == 0.0:
        return img
    if v > 0.0:
        p = 1.0 + v * 5.0
        out = 1.0 - np.power(np.clip(1.0 - img, 0.0, 1.0), p, dtype=np.float32)
    else:
        p = 1.0 / (1.0 - v * 2.5)
        base = img + v * 0.01
        out = 1.0 - np.power(np.clip(1.0 - base, 0.0, 1.0), p, dtype=np.float32)
    return np.clip(out, 0.0, 1.0, out=out)


# --------------------------------------------------------------------------- #
# contrast                                   (SeekModeScript.lua + colorAdjust.frag)
# --------------------------------------------------------------------------- #
def _sigmoid_x_factor(contrast: float) -> float:
    return math.exp(8.33 * contrast - 12.16) + 5.82 * contrast - 1.72


def _sigmoid(x: np.ndarray, a: float, pivot: float) -> Tuple[np.ndarray, np.ndarray]:
    s = 1.0 / (1.0 + np.exp(-a * (x - pivot)))
    return s + pivot - 0.5, a * s * (1.0 - s)


def contrast(img: np.ndarray, value: float, pivot: float = CONTRAST_PIVOT) -> np.ndarray:
    """``value`` in [-1, 1]; 0 is identity.

    ``SeekModeScript``: contrast = 1 + v*0.4 (v<=0) or 1 + v*0.6 (v>0).
    For contrast <= 1 the shader applies a linear stretch about ``pivot``;
    above 1 it applies a normalised sigmoid whose slope is corrected so that
    0 and 1 stay anchored.
    """
    v = float(np.clip(value, -1.0, 1.0))
    if v == 0.0:
        return img
    c = (v * 0.4 + 1.0) if v <= 0.0 else (v * 0.6 + 1.0)

    if c <= 1.0:
        out = c * (img - pivot) + pivot
        return np.clip(out, 0.0, 1.0, out=out)

    a = _sigmoid_x_factor(c)
    _, k0 = _sigmoid(np.float32(0.0), a, pivot)
    _, k1 = _sigmoid(np.float32(1.0), a, pivot)
    y0, _ = _sigmoid(np.float32(0.0), a, pivot)
    y1, _ = _sigmoid(np.float32(1.0), a, pivot)
    yp, kp = _sigmoid(np.float32(pivot), a, pivot)

    y = np.empty_like(img)
    for ch in range(3):
        x = img[..., ch]
        base, k = _sigmoid(x, a, pivot)
        left = x <= pivot
        scale_l = ((kp - k) / (kp - k0)) ** 2
        scale_r = ((kp - k) / (kp - k1)) ** 2
        base = np.where(left, base + scale_l * (0.0 - y0), base + scale_r * (1.0 - y1))
        y[..., ch] = base
    return np.clip(y, 0.0, 1.0, out=y)


# --------------------------------------------------------------------------- #
# saturation                                 (colorAdjust.frag, u_enableSaturation)
# --------------------------------------------------------------------------- #
def saturation(img: np.ndarray, value: float) -> np.ndarray:
    """``value`` is the shader's ``u_saturationParam``: 1.0 is identity.

    Slider mapping observed in ``saturation_v1/ImageBusinessSlider.lua``:
    ``param = (intensity * 0.5 + 0.5) * 100`` for intensity in [-1, 1], i.e. a
    0..100 UI value which the shader consumes scaled to 0..2.
    """
    s = float(value)
    if s == 1.0:
        return img
    base = SATURATION_LUMINANCE * (1.0 - s)
    m = np.eye(3, dtype=np.float32) * s + np.ones((3, 3), dtype=np.float32) * base
    out = img @ m.T
    return np.clip(out, 0.0, 1.0, out=out)


# --------------------------------------------------------------------------- #
# highlight / shadow                        (colorAdjust.frag adjust{Highlight,Shadow})
# --------------------------------------------------------------------------- #
def highlight(img: np.ndarray, value: float) -> np.ndarray:
    """``value`` in [-1, 1] mapped to the exponent ``p = 1 + value``."""
    p = 1.0 + float(np.clip(value, -1.0, 1.0))
    if p == 1.0:
        return img
    t = np.clip(1.0 - img, 0.0, 1.0)
    t2 = t * t
    t3 = t2 * t
    res = 1.0 - np.power(t, p, dtype=np.float32) - (p - 1.0) * (t2 - t3)
    if p < 1.0:
        res = res - abs(1.0 - p) * 0.2 * np.power(np.clip(res, 0.0, 1.0), 10.0)
    return np.clip(res, 0.0, 1.0, out=res)


def shadow(img: np.ndarray, value: float) -> np.ndarray:
    """``value`` in [-1, 1] mapped to the exponent ``p = 1 + value``."""
    p = 1.0 + float(np.clip(value, -1.0, 1.0))
    if p == 1.0:
        return img
    c2 = img * img
    c3 = c2 * img
    res = np.power(np.clip(img, 0.0, 1.0), p, dtype=np.float32) + (p - 1.0) * (c2 - c3)
    if p < 1.0:
        res = res + abs(1.0 - p) * 0.2 * np.power(np.clip(res - 1.0, -1.0, 0.0), 10.0)
    return np.clip(res, 0.0, 1.0, out=res)


# --------------------------------------------------------------------------- #
# white / black levels                          (black_white/AmazingFeature/lua/Lscript.lua)
# --------------------------------------------------------------------------- #
MAX_LEVEL_RANGE = 0.5
LEVEL_EPS = 0.005


def levels_matrix(white: float, black: float) -> Tuple[float, float]:
    """Return ``(slope, bias)`` for ``y = slope * x + bias``."""
    if white >= 0.0:
        xw, yw = 1.0 - white * MAX_LEVEL_RANGE, 1.0
    else:
        xw, yw = 1.0, 1.0 + white * MAX_LEVEL_RANGE
    if black >= 0.0:
        xb, yb = 0.0, black * MAX_LEVEL_RANGE
    else:
        xb, yb = -black * MAX_LEVEL_RANGE, 0.0
    xw = max(xw, LEVEL_EPS)
    yw = max(yw, LEVEL_EPS)
    xb = min(xb, xw - LEVEL_EPS)
    yb = min(yb, yw - LEVEL_EPS)
    slope = (yw - yb) / (xw - xb)
    bias = yb - slope * xb
    return slope, bias


def black_white(img: np.ndarray, white: float = 0.0, black: float = 0.0) -> np.ndarray:
    if white == 0.0 and black == 0.0:
        return img
    slope, bias = levels_matrix(white, black)
    out = slope * img + bias
    return np.clip(out, 0.0, 1.0, out=out)


# --------------------------------------------------------------------------- #
# temperature / tint                          (temperature_tone_v3/AmazingFeature/lua/Lscript.lua)
# --------------------------------------------------------------------------- #
# The app's hue-adaptive CCT model: (mired-ish, x, y, slope) control points.
_CCT_TABLE = (
    0, 0.18006, 0.26352, -0.24341,
    10, 0.18066, 0.26589, -0.25479,
    20, 0.18133, 0.26846, -0.26876,
    30, 0.18208, 0.27119, -0.28539,
    40, 0.18293, 0.27407, -0.30470,
    50, 0.18388, 0.27709, -0.32675,
    60, 0.18494, 0.28021, -0.35156,
    70, 0.18611, 0.28342, -0.37915,
    80, 0.18740, 0.28668, -0.40955,
    90, 0.18880, 0.28997, -0.44278,
    100, 0.19032, 0.29326, -0.47888,
    125, 0.19462, 0.30141, -0.58204,
    150, 0.19962, 0.30921, -0.70471,
    175, 0.20525, 0.31647, -0.84901,
    200, 0.21142, 0.32312, -1.0182,
    225, 0.21807, 0.32909, -1.2168,
    250, 0.22511, 0.33439, -1.4512,
    275, 0.23247, 0.33904, -1.7298,
    300, 0.24010, 0.34308, -2.0637,
    325, 0.24792, 0.34655, -2.4681,
    350, 0.25591, 0.34951, -2.9641,
    375, 0.26400, 0.35200, -3.5814,
    400, 0.27218, 0.35407, -4.3633,
    425, 0.28039, 0.35577, -5.3762,
    450, 0.28863, 0.35714, -6.7262,
    475, 0.29685, 0.35823, -8.5955,
    500, 0.30505, 0.35907, -11.324,
    525, 0.31320, 0.35968, -15.628,
    550, 0.32129, 0.36011, -23.325,
    575, 0.32931, 0.36038, -40.770,
    600, 0.33724, 0.36051, -116.45,
)

_A_MAT = np.array(
    [[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]],
    dtype=np.float64,
)
_B_MAT = np.array(
    [[0.987, -0.1471, 0.16], [0.4323, 0.5184, 0.0493], [-0.0085, 0.04, 0.9685]],
    dtype=np.float64,
)
_P_MAT = np.array(
    [
        [0.4123907992659595, 0.357584339383878, 0.1804807884018343],
        [0.21263900587151036, 0.715168678767756, 0.07219231536073371],
        [0.019330818715591832, 0.11919477979462598, 0.9505321522496607],
    ],
    dtype=np.float64,
)
_Q_MAT = np.array(
    [
        [3.2409699419045213, -1.5373831775700935, -0.4986107602930033],
        [-0.9692436362808796, 1.8759675015077208, 0.04155505740717562],
        [0.05563007969699364, -0.20397695888897655, 1.0569715142428784],
    ],
    dtype=np.float64,
)


def _cct_vec3(cct: float, tint: float) -> np.ndarray:
    """Direct port of ``Lscript:vec3Compute``."""
    data = _CCT_TABLE
    n = len(data)
    x = 1000000.0 / cct
    y = tint * 0.0001

    index = 5
    datum = data[index]
    while datum <= x and index + 4 < n:
        datum = data[index]
        index += 4
    # ``index`` now points at the first entry with data >= x (or the last one)
    prev = index - 4
    if index >= n:
        index = n - 4
        prev = index - 4
    datum = data[index]
    factor = (datum - x) / (datum - data[prev])

    temp_1 = (
        data[index - 3] * (1 - factor) + data[prev - 3] * factor,
        data[index - 2] * (1 - factor) + data[prev - 2] * factor,
    )
    a = data[index - 1]
    b = data[prev - 1]
    sq_a = math.sqrt(a * a + 1.0)
    sq_b = math.sqrt(b * b + 1.0)
    t21 = (1.0 / sq_b) * (1 - factor) + (1.0 / sq_a) * factor
    t22 = (b / sq_b) * (1 - factor) + (a / sq_a) * factor
    norm = math.sqrt(t21 * t21 + t22 * t22)
    t11 = y * t21 / norm + temp_1[0]
    t12 = y * t22 / norm + temp_1[1]

    denom = -4.0 * t12 + t11 + 2.0
    aa = t11 * 1.5 / denom
    bb = t12 / denom
    aa = min(max(aa, 0.000001), 0.999999)
    bb = min(max(bb, 0.000001), 0.999999)
    if aa + bb > 0.999999:
        t = 0.999999 / (aa + bb)
        aa = aa / t
        bb = bb / t
    return np.array([aa / bb, 1.0, (1.0 - aa - bb) / bb], dtype=np.float64)


def temperature_matrix(value: float, tint: float) -> np.ndarray:
    """Build the 3x3 white-balance matrix for a -1..1 temperature/tint pair.

    ``Lscript:onEvent``: ``ColorTemperature = 6500 - 1970x + 876x^2 - 2630x^3``
    and ``ColorTones = tint * 100``.
    """
    x = float(np.clip(value, -1.0, 1.0))
    cct = 6500.0 - 1970.0 * x + 876.0 * x * x - 2630.0 * x * x * x
    tones = float(np.clip(tint, -1.0, 1.0)) * 100.0
    if abs(x) < 1e-9 and abs(tones) < 1e-9:
        return np.eye(3, dtype=np.float64)
    v1 = _cct_vec3(cct, tones)
    v2 = _cct_vec3(6500.0, 0.0)
    x1 = _A_MAT @ v1
    x2 = _A_MAT @ v2
    D = np.diag([x1[0] / x2[0], x1[1] / x2[1], x1[2] / x2[2]])
    m = _Q_MAT @ (_P_MAT @ (_B_MAT @ (D @ _A_MAT)))
    # The shader does ``dot(rgb, u_RVec3)`` so each *row* of the Lua matrix is
    # the output channel's weight vector.
    return m


def temperature_tint(img: np.ndarray, value: float = 0.0, tint: float = 0.0) -> np.ndarray:
    if value == 0.0 and tint == 0.0:
        return img
    m = temperature_matrix(value, tint).astype(np.float32)
    out = img @ m.T
    return np.clip(out, 0.0, 1.0, out=out)


# --------------------------------------------------------------------------- #
# sharpen / blur / vignette
# --------------------------------------------------------------------------- #
def _box_blur(img: np.ndarray, radius: int) -> np.ndarray:
    if radius < 1:
        return img
    r = int(radius)
    k = 2 * r + 1
    # float32 的累积和在 1080p 上也远低于 2^24，做模糊足够；比 float64 快一倍
    src = img if img.dtype == np.float32 else img.astype(np.float32)
    pad = np.pad(src, ((r, r), (r, r), (0, 0)), mode="edge")
    c = np.cumsum(pad, axis=0, dtype=np.float32)
    c = np.concatenate([np.zeros((1,) + c.shape[1:], dtype=c.dtype), c], axis=0)
    v = (c[k:] - c[:-k]) / k
    c = np.cumsum(v, axis=1, dtype=np.float32)
    c = np.concatenate([np.zeros((c.shape[0], 1) + c.shape[2:], dtype=c.dtype), c], axis=1)
    out = (c[:, k:] - c[:, :-k]) / k
    return out


def sharpen(img: np.ndarray, amount: float) -> np.ndarray:
    """Unsharp mask; ``amount`` 0..1 maps to a radius-1..2, gain 0..1 kernel.

    (JianyingPro's implementation is a GPU convolution; a normalised unsharp
    mask reproduces the visible result.)
    """
    a = float(np.clip(amount, 0.0, 1.0))
    if a <= 0.0:
        return img
    blurred = _box_blur(img, 1)
    out = img + a * 2.0 * (img - blurred)
    return np.clip(out, 0.0, 1.0, out=out)


def blur(img: np.ndarray, radius: float) -> np.ndarray:
    r = int(round(float(radius)))
    if r < 1:
        return img
    return _box_blur(img, r)


def vignette(img: np.ndarray, amount: float, feather: float = 0.55) -> np.ndarray:
    """Radial darkening; ``amount`` > 0 darkens the corners."""
    a = float(np.clip(amount, 0.0, 1.0))
    if a <= 0.0:
        return img
    h, w = img.shape[:2]
    yy = np.linspace(-1.0, 1.0, h, dtype=np.float32)[:, None]
    xx = np.linspace(-1.0, 1.0, w, dtype=np.float32)[None, :]
    r = np.sqrt(xx * xx + yy * yy) / math.sqrt(2.0)
    f = max(1e-3, float(feather))
    mask = np.clip((r - (1.0 - f)) / f, 0.0, 1.0)
    mask = 1.0 - a * (mask * mask * (3.0 - 2.0 * mask))
    out = img * mask[..., None]
    return np.clip(out, 0.0, 1.0, out=out)


# --------------------------------------------------------------------------- #
# 3D LUT
# --------------------------------------------------------------------------- #
def load_cube_lut(path: str) -> np.ndarray:
    """Load an Adobe ``.cube`` 3D LUT as an ``(N, N, N, 3)`` float32 array."""
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        lines = fh.readlines()
    size = 0
    values = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head = line.split()
        if head[0].upper() == "LUT_3D_SIZE":
            size = int(head[1])
            continue
        if head[0].upper() in ("TITLE", "DOMAIN_MIN", "DOMAIN_MAX", "LUT_1D_SIZE"):
            continue
        if len(head) >= 3:
            try:
                values.append((float(head[0]), float(head[1]), float(head[2])))
            except ValueError:
                continue
    if size <= 0:
        raise ValueError(f"not a 3D .cube LUT: {path}")
    arr = np.asarray(values[: size ** 3], dtype=np.float32).reshape((size, size, size, 3))
    return arr


def apply_lut3d(img: np.ndarray, lut: np.ndarray, intensity: float = 1.0) -> np.ndarray:
    """Trilinear interpolated look-up (red fastest, then green, then blue)."""
    n = lut.shape[0]
    if n < 2:
        return img
    x = np.clip(img, 0.0, 1.0) * (n - 1)
    i0 = np.floor(x).astype(np.int32)
    i1 = np.clip(i0 + 1, 0, n - 1)
    i0 = np.clip(i0, 0, n - 1)
    f = (x - i0).astype(np.float32)

    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    r1, g1, b1 = i1[..., 0], i1[..., 1], i1[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]

    c000 = lut[b0, g0, r0]
    c100 = lut[b0, g0, r1]
    c010 = lut[b0, g1, r0]
    c110 = lut[b0, g1, r1]
    c001 = lut[b1, g0, r0]
    c101 = lut[b1, g0, r1]
    c011 = lut[b1, g1, r0]
    c111 = lut[b1, g1, r1]

    c00 = c000 + (c100 - c000) * fr
    c01 = c010 + (c110 - c010) * fr
    c10 = c001 + (c101 - c001) * fr
    c11 = c011 + (c111 - c011) * fr
    c0 = c00 + (c01 - c00) * fg
    c1 = c10 + (c11 - c10) * fg
    out = c0 + (c1 - c0) * fb

    amt = float(np.clip(intensity, 0.0, 1.0))
    if amt < 1.0:
        out = img + (out - img) * amt
    return np.clip(out, 0.0, 1.0, out=out)


# --------------------------------------------------------------------------- #
# blend modes                             (Resources/MixMode/MixMode.json)
# --------------------------------------------------------------------------- #
#: nameId -> human label, in the order the app exposes them.
BLEND_MODES = {
    "multiply_blend_mode": "正片叠底",
    "color_dodge": "颜色减淡",
    "darken_color": "变暗",
    "linear_deepening": "线性加深",
    "soft_light": "柔光",
    "glare_pc": "强光",
    "over_lay": "叠加",
    "bright_en": "变亮",
    "dark_en": "变暗（英文）",
    "color_filter": "颜色过滤",
    "screen": "滤色",
    "color_burn": "颜色加深",
}

#: the app names the blend mode with a Chinese label in ``mix_mode.name``
BLEND_NAME_TO_ID = {
    "正片叠底": "multiply_blend_mode",
    "颜色减淡": "color_dodge",
    "颜色加深": "color_burn",
    "线性加深": "linear_deepening",
    "柔光": "soft_light",
    "强光": "glare_pc",
    "滤色": "screen",
    "叠加": "over_lay",
    "变亮": "bright_en",
    "变暗": "dark_en",
    "正常": "normal",
}


def blend(base: np.ndarray, layer: np.ndarray, mode: str = "normal") -> np.ndarray:
    """Composite ``layer`` over ``base`` (both RGB float arrays, same shape)."""
    if mode in ("", "normal", None):
        return layer
    b = base
    s = layer
    if mode == "multiply_blend_mode":
        return b * s
    if mode == "screen":
        return 1.0 - (1.0 - b) * (1.0 - s)
    if mode == "color_burn":
        return np.where(
            s <= 0.0, 0.0, np.clip(1.0 - (1.0 - b) / np.maximum(s, 1e-6), 0.0, 1.0)
        )
    if mode == "color_dodge":
        return np.where(s >= 1.0, 1.0, np.minimum(1.0, b / np.maximum(1e-6, 1.0 - s)))
    if mode == "darken_color":
        return np.minimum(b, s)
    if mode == "linear_deepening":
        return np.clip(b + s - 1.0, 0.0, 1.0)
    if mode == "soft_light":
        return np.where(
            s <= 0.5,
            b - (1.0 - 2.0 * s) * b * (1.0 - b),
            b + (2.0 * s - 1.0) * (np.sqrt(np.maximum(b, 0.0)) - b),
        )
    if mode == "glare_pc":
        return np.where(s <= 0.5, 2.0 * b * s, 1.0 - 2.0 * (1.0 - b) * (1.0 - s))
    if mode == "over_lay":
        return np.where(b <= 0.5, 2.0 * b * s, 1.0 - 2.0 * (1.0 - b) * (1.0 - s))
    if mode == "bright_en":
        return np.maximum(b, s)
    return layer


# --------------------------------------------------------------------------- #
# curves                                    (AmazingFeature_curves, zorder 5017)
# --------------------------------------------------------------------------- #
#: hue centres of the 8 HSL bands the app exposes, in degrees
HSL_BANDS = (
    ("red", 0.0),
    ("orange", 30.0),
    ("yellow", 60.0),
    ("green", 120.0),
    ("cyan", 180.0),
    ("blue", 240.0),
    ("purple", 280.0),
    ("magenta", 320.0),
)


def _curve_lut(points: Sequence[Sequence[float]], n: int = 256) -> np.ndarray:
    pts = [(float(x), float(y)) for x, y in (points or []) if len((x, y)) == 2]
    if len(pts) < 2:
        return np.linspace(0.0, 1.0, n, dtype=np.float32)
    pts.sort()
    xs = np.array([p[0] for p in pts], dtype=np.float32)
    ys = np.array([p[1] for p in pts], dtype=np.float32)
    grid = np.linspace(0.0, 1.0, n, dtype=np.float32)
    return np.interp(grid, xs, ys).astype(np.float32)


def _apply_curve_lut(channel: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Sample a 0..1 tone curve with linear interpolation (exact for identity)."""
    n = lut.shape[0]
    x = np.clip(channel, 0.0, 1.0) * (n - 1)
    i0 = np.floor(x).astype(np.int32)
    i1 = np.clip(i0 + 1, 0, n - 1)
    i0 = np.clip(i0, 0, n - 1)
    f = (x - i0).astype(np.float32)
    return (lut[i0] * (1.0 - f) + lut[i1] * f).astype(np.float32)


def apply_curves(img: np.ndarray, curves: dict) -> np.ndarray:
    """Apply RGB / per-channel curves.

    ``curves`` maps a channel to control points in 0..1::

        {"rgb": [[0, 0], [0.5, 0.55], [1, 1]], "r": [...], "g": [...], "b": [...]}

    The app's curves panel is a spline editor; we sample the authored points
    and interpolate linearly, which reproduces the same tone mapping when the
    points are dense enough.
    """
    if not curves:
        return img
    out = img.astype(np.float32, copy=True)
    master = curves.get("rgb") or curves.get("master")
    if master:
        lut = _curve_lut(master)
        for i in range(3):
            out[..., i] = _apply_curve_lut(out[..., i], lut)
    for i, ch in enumerate(("r", "g", "b")):
        pts = curves.get(ch)
        if not pts:
            continue
        out[..., i] = _apply_curve_lut(out[..., i], _curve_lut(pts))
    return np.clip(out, 0.0, 1.0, out=out)


# --------------------------------------------------------------------------- #
# HSL                                       (AmazingFeature_hsl, zorder 5016)
# --------------------------------------------------------------------------- #
def _rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    diff = mx - mn
    h = np.zeros_like(mx)
    mask = diff > 1e-8
    rm = mask & (mx == r)
    gm = mask & (mx == g) & ~rm
    bm = mask & (mx == b) & ~rm & ~gm
    h[rm] = ((g[rm] - b[rm]) / diff[rm]) % 6.0
    h[gm] = (b[gm] - r[gm]) / diff[gm] + 2.0
    h[bm] = (r[bm] - g[bm]) / diff[bm] + 4.0
    h = h * 60.0
    s = np.where(mx > 1e-8, diff / np.maximum(mx, 1e-8), 0.0)
    return np.stack([h, s, mx], axis=-1).astype(np.float32)


def _hsv_to_rgb(hsv: np.ndarray) -> np.ndarray:
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    c = v * s
    hh = (h % 360.0) / 60.0
    x = c * (1.0 - np.abs((hh % 2.0) - 1.0))
    m = v - c
    z = np.zeros_like(c)
    r = np.select([hh < 1, hh < 2, hh < 3, hh < 4, hh < 5, hh < 6], [c, x, z, z, x, c], default=c)
    g = np.select([hh < 1, hh < 2, hh < 3, hh < 4, hh < 5, hh < 6], [x, c, c, x, z, z], default=c)
    b = np.select([hh < 1, hh < 2, hh < 3, hh < 4, hh < 5, hh < 6], [z, z, x, c, c, x], default=c)
    return np.clip(np.stack([r + m, g + m, b + m], axis=-1), 0.0, 1.0).astype(np.float32)


def apply_hsl(img: np.ndarray, bands: dict, fallback: Optional[dict] = None) -> np.ndarray:
    """Per-hue-band hue / saturation / luminance offsets.

    ``bands`` maps a band name (see :data:`HSL_BANDS`) to
    ``{"hue": deg, "sat": -1..1, "lum": -1..1}``; ``fallback`` (keys
    ``hue``/``sat``/``lum``) applies to every band.
    """
    if not bands and not fallback:
        return img
    hsv = _rgb_to_hsv(img)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    hue_shift = np.zeros_like(h)
    sat_gain = np.zeros_like(s)
    lum_gain = np.zeros_like(v)

    if fallback:
        hue_shift += float(fallback.get("hue", 0.0) or 0.0)
        sat_gain += float(fallback.get("sat", 0.0) or 0.0)
        lum_gain += float(fallback.get("lum", 0.0) or 0.0)

    for name, centre in HSL_BANDS:
        band = (bands or {}).get(name)
        if not band:
            continue
        d = np.abs(((h - centre + 180.0) % 360.0) - 180.0)
        w = np.clip(1.0 - d / 60.0, 0.0, 1.0)
        hue_shift += w * float(band.get("hue", 0.0) or 0.0)
        sat_gain += w * float(band.get("sat", 0.0) or 0.0)
        lum_gain += w * float(band.get("lum", 0.0) or 0.0)

    h = (h + hue_shift) % 360.0
    s = np.clip(s * (1.0 + sat_gain), 0.0, 1.0)
    v = np.clip(v * (1.0 + lum_gain), 0.0, 1.0)
    return _hsv_to_rgb(np.stack([h, s, v], axis=-1))


# --------------------------------------------------------------------------- #
# primary colour wheels                  (AmazingFeature_primaryWheel, zorder 5019)
# --------------------------------------------------------------------------- #
def apply_wheels(img: np.ndarray, wheels: dict) -> np.ndarray:
    """Lift / gamma / gain grading.

    ``wheels`` may contain any of::

        {"lift":  [dr, dg, db],   # additive offset weighted towards shadows
         "gamma": [gr, gg, gb],   # per-channel exponent, 1.0 = neutral
         "gain":  [mr, mg, mb]}   # per-channel multiplier, 1.0 = neutral

    This is the classic ASC-CDL style split (shadows / midtones / highlights)
    that the app's Primary Wheel panel exposes.
    """
    if not wheels:
        return img
    out = img.astype(np.float32, copy=True)
    lift = wheels.get("lift")
    if lift:
        d = np.asarray(lift, dtype=np.float32)
        # shadows: (1 - x) weighting keeps highlights untouched
        out = out + d[None, None, :] * (1.0 - out)
    gamma = wheels.get("gamma")
    if gamma:
        g = np.maximum(np.asarray(gamma, dtype=np.float32), 1e-3)
        out = np.power(np.clip(out, 1e-6, 1.0), 1.0 / g[None, None, :])
    gain = wheels.get("gain")
    if gain:
        m = np.asarray(gain, dtype=np.float32)
        out = out * m[None, None, :]
    return np.clip(out, 0.0, 1.0, out=out)


# --------------------------------------------------------------------------- #
# full adjustment stack
# --------------------------------------------------------------------------- #
#: Parameter names, ranges and defaults of the "调节" (adjust) panel.  Ranges
#: come from the app's own slider metadata (``ImageBusinessSlider.json``:
#: ``minValue``/``maxValue``) and its UI (-50..50 for most sliders).
ADJUST_SPEC = {
    "brightness": {"min": -50.0, "max": 50.0, "default": 0.0, "label": "亮度"},
    "contrast": {"min": -50.0, "max": 50.0, "default": 0.0, "label": "对比度"},
    "saturation": {"min": -50.0, "max": 50.0, "default": 0.0, "label": "饱和度"},
    "temperature": {"min": -50.0, "max": 50.0, "default": 0.0, "label": "色温"},
    "tint": {"min": -50.0, "max": 50.0, "default": 0.0, "label": "色调"},
    "highlight": {"min": -50.0, "max": 50.0, "default": 0.0, "label": "高光"},
    "shadow": {"min": -50.0, "max": 50.0, "default": 0.0, "label": "阴影"},
    "white": {"min": -50.0, "max": 50.0, "default": 0.0, "label": "白色"},
    "black": {"min": -50.0, "max": 50.0, "default": 0.0, "label": "黑色"},
    "sharpen": {"min": 0.0, "max": 50.0, "default": 0.0, "label": "锐化"},
    "vignette": {"min": 0.0, "max": 50.0, "default": 0.0, "label": "暗角"},
}


def _apply_adjust_direct(img: np.ndarray, params: dict, lut: Optional[np.ndarray] = None) -> np.ndarray:
    """Reference implementation: evaluate every stage per pixel (slow but exact)."""
    if not params:
        return img
    get = lambda k: float(params.get(k, 0.0) or 0.0)  # noqa: E731

    out = img
    if lut is not None:
        out = apply_lut3d(out, lut, 1.0)
    out = brightness(out, get("brightness") / 50.0)
    out = contrast(out, get("contrast") / 50.0)
    out = saturation(out, 1.0 + get("saturation") / 50.0)
    out = highlight(out, get("highlight") / 50.0)
    out = shadow(out, get("shadow") / 50.0)
    out = black_white(out, get("white") / 50.0, get("black") / 50.0)
    out = temperature_tint(out, get("temperature") / 50.0, get("tint") / 50.0)
    out = sharpen(out, get("sharpen") / 50.0)
    out = vignette(out, get("vignette") / 50.0)
    return out


#: Smooth, per-channel stages are tabulated.  A 1024-step table reproduces them
#: to ~0.002 while replacing a per-pixel sigmoid evaluation with one gather.
#: Stages whose exponent can drop below 1 (highlight/shadow) are NOT tabulated:
#: ``x**0.2`` has an infinite slope at 0, so quantisation near black gets
#: amplified (measured 0.07 error).
ADJUST_LUT_SIZE = 1024
_ADJUST_LUT_CACHE: Dict[tuple, np.ndarray] = {}


def _ramp(n: int = ADJUST_LUT_SIZE) -> np.ndarray:
    x = np.linspace(0.0, 1.0, n, dtype=np.float32).reshape(n, 1, 1)
    return np.repeat(x, 3, axis=2)


def _separable_lut(key: tuple, fn, n: int = ADJUST_LUT_SIZE) -> np.ndarray:
    cached = _ADJUST_LUT_CACHE.get(key)
    if cached is not None:
        return cached
    probe = fn(_ramp(n))                       # (n, 1, 3): each channel's response
    lut = np.ascontiguousarray(probe[:, 0, :].T, dtype=np.float32)   # (3, n)
    if len(_ADJUST_LUT_CACHE) > 64:
        _ADJUST_LUT_CACHE.clear()
    _ADJUST_LUT_CACHE[key] = lut
    return lut


def _apply_channel_lut(img: np.ndarray, lut: np.ndarray) -> np.ndarray:
    n = lut.shape[1]
    idx = np.clip(img * (n - 1) + 0.5, 0, n - 1).astype(np.int32)
    offsets = (np.arange(3, dtype=np.int32) * n).reshape(1, 1, 3)
    return lut.ravel()[idx + offsets]


def apply_adjust(img: np.ndarray, params: dict, lut: Optional[np.ndarray] = None) -> np.ndarray:
    """Apply the app's adjustment chain in the shader's fixed order.

    ``params`` holds UI slider values in the app's units (-100..100, see
    :data:`ADJUST_SPEC`).  Pointwise stages go through cached tables; see
    :func:`_apply_adjust_direct` for the exact per-pixel reference.

    输入统一转成 float32：渲染管线内部本来就是 float，但外部调用（脚本、测试）
    常常直接传 uint8 图像，之前会在这里崩掉（``OverflowError: 1023 out of bounds``）。
    """
    img = img if img.dtype == np.float32 else img.astype(np.float32)
    if img.max() > 1.5:
        img = img / 255.0

    if not params:
        return img
    get = lambda k: float(params.get(k, 0.0) or 0.0)  # noqa: E731

    b = get("brightness") / 50.0
    c = get("contrast") / 50.0
    s = 1.0 + get("saturation") / 50.0
    hi = get("highlight") / 50.0
    sh = get("shadow") / 50.0
    w = get("white") / 50.0
    bl = get("black") / 50.0
    temp = get("temperature") / 50.0
    tint = get("tint") / 50.0
    sharp = get("sharpen") / 50.0
    vig = get("vignette") / 50.0

    # fixed shader order: brightness -> contrast -> saturation -> highlight ->
    # shadow -> levels -> temperature -> sharpen -> vignette
    out = img
    if lut is not None:
        out = apply_lut3d(out, lut, 1.0)
    if b or c:
        out = _apply_channel_lut(
            out, _separable_lut(("bc", round(b, 6), round(c, 6)),
                                lambda x: contrast(brightness(x, b), c)))
    if s != 1.0:
        out = saturation(out, s)
    if hi:
        out = highlight(out, hi)
    if sh:
        out = shadow(out, sh)
    if w or bl:
        out = _apply_channel_lut(
            out, _separable_lut(("levels", round(w, 6), round(bl, 6)),
                                lambda x: black_white(x, w, bl)))
    if temp or tint:
        out = _apply_channel_lut(
            out, _separable_lut(("temp", round(temp, 6), round(tint, 6)),
                                lambda x: temperature_tint(x, temp, tint)))
    if sharp:
        out = sharpen(out, sharp)
    if vig:
        out = vignette(out, vig)
    return out

def apply_hsl_curves(img: np.ndarray, bands: dict) -> np.ndarray:
    """Per-hue-band tone curves (剪映 ``AmazingFeature_hsl_curves``, zorder 5018).

    ``bands`` maps a band name (see :data:`HSL_BANDS`) to
    ``{"points": [[x, y], ...], "channel": "luma" | "r" | "g" | "b"}``.
    A pixel is affected in proportion to how close its hue is to the band centre
    (same 60° falloff as :func:`apply_hsl`), so bands blend instead of cutting.

    **Evidence level**: the bundle name and its zorder slot are observed; the
    per-band curve maths is not in the extracted shaders, so this is a
    reimplementation of "curves, per band" rather than a translation.
    """
    if not bands:
        return img
    hsv = _rgb_to_hsv(img)
    hue = hsv[..., 0]
    luma = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]

    channels = {"r": 0, "g": 1, "b": 2}
    out = img
    for name, spec in (bands or {}).items():
        centre = dict(HSL_BANDS).get(name)
        points = (spec or {}).get("points") or []
        if centre is None or len(points) < 2:
            continue
        d = np.abs(((hue - centre + 180.0) % 360.0) - 180.0)
        weight = np.clip(1.0 - d / 60.0, 0.0, 1.0)
        mask = np.any(weight > 0)
        if not mask:
            continue
        channel = str((spec or {}).get("channel") or "luma")
        idx = channels.get(channel)
        source = luma if idx is None else out[..., idx]
        mapped = _eval_curve(source, points)
        if idx is None:
            # 亮度曲线：保持色相/饱和度，只把亮度按权重混合
            out = np.clip(out + (mapped - source)[..., None] * weight[..., None], 0.0, 1.0)
        else:
            blended = source + (mapped - source) * weight
            out = np.array(out, copy=True)
            out[..., idx] = blended
            out = np.clip(out, 0.0, 1.0)
    return out


def _eval_curve(values: np.ndarray, points) -> np.ndarray:
    """Piecewise-linear curve through normalised ``[x, y]`` control points."""
    xs = np.array([float(p[0]) for p in points], dtype=np.float32)
    ys = np.array([float(p[1]) for p in points], dtype=np.float32)
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]
    return np.interp(values, xs, ys).astype(np.float32)

def build_transfer_lut(reference_rgb: np.ndarray, source_rgb: np.ndarray,
                       size: int = 17) -> np.ndarray:
    """Bake a 3D LUT that moves ``source_rgb``'s colour statistics to ``reference``'s.

    JianyingPro's "色彩迁移" (``AmazingFeature_colorMigration``, zorder 5010) is
    a **baked 8x8-in-16x8 LUT** plus a single ``u_intensity`` uniform — the shader
    we extracted (``colormigration.frag``) is plain trilinear sampling of that
    LUT.  So migration is a colour-transfer transform, not a model: we compute
    per-channel mean/σ from both images and bake the mapping into a LUT grid.

    Returns an ``(size, size, size, 3)`` float32 LUT in 0..1, ready for
    :func:`apply_lut3d`.
    """
    ref = np.asarray(reference_rgb, dtype=np.float32).reshape(-1, 3)
    src = np.asarray(source_rgb, dtype=np.float32).reshape(-1, 3)
    if ref.size == 0 or src.size == 0:
        raise ValueError("build_transfer_lut 需要两张非空图像")
    ref_mean, ref_std = ref.mean(axis=0), ref.std(axis=0) + 1e-6
    src_mean, src_std = src.mean(axis=0), src.std(axis=0) + 1e-6

    grid = np.linspace(0.0, 1.0, size, dtype=np.float32)
    r, g, b = np.meshgrid(grid, grid, grid, indexing="ij")
    rgb = np.stack([r, g, b], axis=-1)
    moved = (rgb - src_mean) / src_std * ref_std + ref_mean
    return np.clip(moved, 0.0, 1.0).astype(np.float32)


def image_stats(rgb: np.ndarray) -> dict:
    """Mean / std per channel — the numbers a migration is actually built from."""
    arr = np.asarray(rgb, dtype=np.float32).reshape(-1, 3)
    return {"mean": arr.mean(axis=0).tolist(), "std": arr.std(axis=0).tolist()}
