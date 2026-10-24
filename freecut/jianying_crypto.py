"""Read/write JianyingPro's encrypted draft JSON by calling the app's own DLL.

JianyingPro 6+ stores ``draft_content.json`` as a base64 blob.  The encoder is
not a plain cipher we have to re-derive: ``videoeditor.dll`` exports

    lvve::EncryptUtils::decrypt(std::string const&, std::string const&, bool&) -> std::string
    lvve::EncryptUtils::encrypt(std::string const&) -> std::string
    lvve::EncryptUtils::enable(bool)

so a locally installed copy of the app can transform its own project files.
This module binds those exports with ``ctypes`` (MSVC x64 ``std::string`` ABI)
and runs every call in a **child process**, because an ABI mismatch would take
down the caller.

Prior art: the symbol names and the MSVC string layout were first published in
``aoguai/pyJianYingDraft`` (Apache-2.0, ``pyJianYingDraft/draft_crypto.py``).
See ``docs/REPORT.md``.  This is an independent implementation of the same
interop idea, used only on the user's own local project files.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

DECRYPT_SYMBOL = (
    "?decrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@"
    "V?$allocator@D@2@@std@@AEBV34@0AEA_N@Z"
)
ENCRYPT_SYMBOL = (
    "?encrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@"
    "V?$allocator@D@2@@std@@AEBV34@@Z"
)
ENABLE_SYMBOL = "?enable@EncryptUtils@lvve@@QEAAX_N@Z"

_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


class DraftCryptoError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# MSVC std::string (x64, 32 bytes)
# --------------------------------------------------------------------------- #
class _StringUnion(ctypes.Union):
    _fields_ = [("small", ctypes.c_char * 16), ("ptr", ctypes.c_void_p)]


class MsvcString(ctypes.Structure):
    _fields_ = [
        ("data", _StringUnion),
        ("size", ctypes.c_ulonglong),
        ("capacity", ctypes.c_ulonglong),
    ]


class StringArg:
    """A ``std::string``-compatible argument whose buffer outlives the call."""

    def __init__(self, data: bytes):
        self._buf = None
        self.value = MsvcString()
        self.value.size = len(data)
        if len(data) < 16:
            self.value.capacity = 15
            ctypes.memset(ctypes.addressof(self.value.data), 0, 16)
            if data:
                ctypes.memmove(ctypes.addressof(self.value.data), data, len(data))
        else:
            self._buf = ctypes.create_string_buffer(data, len(data))
            self.value.capacity = len(data)
            self.value.data.ptr = ctypes.cast(self._buf, ctypes.c_void_p).value


def _take_string(value: MsvcString) -> bytes:
    size = int(value.size)
    if size < 0 or size > (1 << 32):
        raise DraftCryptoError(f"unexpected string size {size}")
    if size == 0:
        return b""
    if value.capacity < 16:
        return ctypes.string_at(ctypes.addressof(value.data), size)
    if not value.data.ptr:
        raise DraftCryptoError("string heap pointer is null")
    return ctypes.string_at(value.data.ptr, size)


# --------------------------------------------------------------------------- #
# install discovery
# --------------------------------------------------------------------------- #
def _candidate_dirs() -> list:
    out = []
    env = os.environ.get("FREECUT_JIANYING_DIR") or os.environ.get("JY_INSTALL_DIR")
    if env:
        out.append(Path(env))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        apps = Path(local) / "JianyingPro" / "apps"
        if apps.is_dir():
            for child in apps.iterdir():
                if child.is_dir() and (child / "videoeditor.dll").is_file():
                    out.append(child)
    for base in (r"C:\Program Files\JianyingPro", r"C:\Program Files (x86)\JianyingPro"):
        p = Path(base)
        if p.is_dir():
            out.append(p)
    return out


def _version_key(path: Path):
    parts = []
    for tok in path.name.replace("-", ".").split("."):
        parts.append(int(tok) if tok.isdigit() else -1)
    return parts


def find_install_dir() -> Path:
    """Newest installed JianyingPro build that ships ``videoeditor.dll``."""
    dirs = [d for d in _candidate_dirs() if (d / "videoeditor.dll").is_file()]
    if not dirs:
        raise DraftCryptoError(
            "找不到剪映安装目录（需要 videoeditor.dll）。"
            "请设置 FREECUT_JIANYING_DIR 指向 apps/<版本> 目录。"
        )
    dirs.sort(key=_version_key)
    return dirs[-1]


# --------------------------------------------------------------------------- #
# in-process binding (only ever used inside the child process)
# --------------------------------------------------------------------------- #
class _Crypto:
    def __init__(self, install_dir: Path):
        if sys.platform != "win32":
            raise DraftCryptoError("剪映草稿加解密只支持 Windows")
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            raise DraftCryptoError("需要 64 位 Python")
        self.dir = Path(install_dir)
        try:
            os.add_dll_directory(str(self.dir))
        except (AttributeError, OSError):
            pass
        self.dll = ctypes.WinDLL(str(self.dir / "videoeditor.dll"))
        self.decrypt = getattr(self.dll, DECRYPT_SYMBOL)
        self.encrypt = getattr(self.dll, ENCRYPT_SYMBOL)
        self.enable = getattr(self.dll, ENABLE_SYMBOL)
        for fn in (self.decrypt, self.encrypt, self.enable):
            fn.restype = None
            fn.argtypes = None

    # -- primitives ---------------------------------------------------------
    def decrypt_bytes(self, data: bytes) -> bytes:
        src = StringArg(data)
        param = StringArg(b"{}")
        out = MsvcString()
        ok = ctypes.c_bool(False)
        self.decrypt(None, ctypes.byref(out), ctypes.byref(src.value),
                     ctypes.byref(param.value), ctypes.byref(ok))
        result = _take_string(out)
        if not ok.value or not result:
            raise DraftCryptoError(f"解密失败 ok={ok.value} len={len(result)}")
        return result

    def encrypt_bytes(self, data: bytes, validate: bool = True) -> bytes:
        self.enable(None, True)
        src = StringArg(data)
        out = MsvcString()
        self.encrypt(None, ctypes.byref(out), ctypes.byref(src.value))
        result = _take_string(out)
        if not result:
            raise DraftCryptoError("加密失败：输出为空")
        if validate and self.decrypt_bytes(result) != data:
            raise DraftCryptoError("加密校验失败：解密回环不一致")
        return result


# --------------------------------------------------------------------------- #
# public API (subprocess-isolated)
# --------------------------------------------------------------------------- #
def _run_child(args: list, timeout: float = 120.0) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "freecut.jianying_crypto", "--child"] + args
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return subprocess.run(cmd, capture_output=True, timeout=timeout, env=env,
                          creationflags=0x08000000 if sys.platform == "win32" else 0)


def _child_main(argv: list) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--child", action="store_true")
    ap.add_argument("--mode", choices=["decrypt", "encrypt", "probe"], required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--install-dir", default="")
    args = ap.parse_args(argv)

    inst = Path(args.install_dir) if args.install_dir else find_install_dir()
    crypto = _Crypto(inst)
    raw = Path(args.src).read_bytes()

    if args.mode == "probe":
        payload = b'{"a":1}'
        enc = crypto.encrypt_bytes(payload, validate=False)
        dec = crypto.decrypt_bytes(enc)
        Path(args.dst).write_bytes(dec)
        print(json.dumps({"encrypted_len": len(enc), "roundtrip_ok": dec == payload}))
        return 0

    out = crypto.decrypt_bytes(raw) if args.mode == "decrypt" else crypto.encrypt_bytes(raw)
    Path(args.dst).write_bytes(out)
    return 0


def decrypt_bytes(data: bytes, install_dir: Optional[Path] = None, timeout: float = 120.0) -> bytes:
    with tempfile.TemporaryDirectory(prefix="freecut-jy-") as tmp:
        src = Path(tmp) / "in.bin"
        dst = Path(tmp) / "out.bin"
        src.write_bytes(data)
        args = ["--mode", "decrypt", "--src", str(src), "--dst", str(dst)]
        if install_dir:
            args += ["--install-dir", str(install_dir)]
        proc = _run_child(args, timeout)
        if proc.returncode != 0 or not dst.is_file():
            raise DraftCryptoError(
                "解密子进程失败: " + proc.stderr.decode("utf-8", "replace")[-800:]
            )
        return dst.read_bytes()


def encrypt_bytes(data: bytes, install_dir: Optional[Path] = None, timeout: float = 120.0) -> bytes:
    with tempfile.TemporaryDirectory(prefix="freecut-jy-") as tmp:
        src = Path(tmp) / "in.bin"
        dst = Path(tmp) / "out.bin"
        src.write_bytes(data)
        args = ["--mode", "encrypt", "--src", str(src), "--dst", str(dst)]
        if install_dir:
            args += ["--install-dir", str(install_dir)]
        proc = _run_child(args, timeout)
        if proc.returncode != 0 or not dst.is_file():
            raise DraftCryptoError(
                "加密子进程失败: " + proc.stderr.decode("utf-8", "replace")[-800:]
            )
        return dst.read_bytes()


def decrypt_file(path, output=None, install_dir: Optional[Path] = None):
    src = Path(path)
    out = Path(output) if output else src.with_suffix(src.suffix + ".dec.json")
    data = decrypt_bytes(src.read_bytes(), install_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out


def encrypt_file(path, output=None, install_dir: Optional[Path] = None):
    src = Path(path)
    out = Path(output) if output else src.with_suffix(src.suffix + ".enc.json")
    data = encrypt_bytes(src.read_bytes(), install_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out


def probe(install_dir: Optional[Path] = None) -> dict:
    """Encrypt a tiny JSON and decrypt it back; proves the binding works."""
    with tempfile.TemporaryDirectory(prefix="freecut-jy-") as tmp:
        src = Path(tmp) / "in.bin"
        dst = Path(tmp) / "out.bin"
        src.write_bytes(b"{}")
        args = ["--mode", "probe", "--src", str(src), "--dst", str(dst)]
        if install_dir:
            args += ["--install-dir", str(install_dir)]
        proc = _run_child(args, 120)
        if proc.returncode != 0:
            raise DraftCryptoError(proc.stderr.decode("utf-8", "replace")[-800:])
        return json.loads(proc.stdout.decode("utf-8", "replace").strip().splitlines()[-1])


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--child" in argv:
        return _child_main(argv)
    ap = argparse.ArgumentParser("freecut.jianying_crypto", description="剪映草稿加解密")
    ap.add_argument("mode", choices=["decrypt", "encrypt", "probe", "where"])
    ap.add_argument("src", nargs="?")
    ap.add_argument("--out")
    ap.add_argument("--install-dir", default="")
    args = ap.parse_args(argv)
    inst = Path(args.install_dir) if args.install_dir else None
    if args.mode == "where":
        print(find_install_dir())
        return 0
    if args.mode == "probe":
        print(json.dumps(probe(inst), ensure_ascii=False))
        return 0
    if not args.src:
        ap.error("需要输入文件")
    if args.mode == "decrypt":
        print(decrypt_file(args.src, args.out, inst))
    else:
        print(encrypt_file(args.src, args.out, inst))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
