#!/usr/bin/env python3
"""
bootstrap.py — 依赖自举 + 转发执行。

让完全没装过 Python 依赖的用户也能双击启动：
  1. 检查 fastapi / uvicorn / httpx / cryptography 是否可用
  2. 缺依赖就在本目录建 .venv 并安装（不污染系统 Python）
  3. 用 .venv 里的解释器重新执行目标脚本

用法： python3 bootstrap.py wb_panel.py [额外参数...]
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
REQUIRED = ("fastapi", "uvicorn", "httpx", "cryptography")


def _have_deps() -> bool:
    for m in REQUIRED:
        try:
            __import__(m)
        except ImportError:
            return False
    return True


def _venv_python() -> Path:
    if sys.platform == "win32":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def _in_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == VENV.resolve()
    except Exception:
        return False


def main():
    if _have_deps() or _in_venv():
        target = sys.argv[1:]
        if not target:
            print("用法: bootstrap.py <脚本.py> [参数...]")
            return 1
        script = HERE / target[0]
        if not script.is_file():
            print(f"找不到脚本: {script}")
            return 1
        return subprocess.call([sys.executable, str(script)] + target[1:])

    print("首次运行，正在准备依赖（只在本目录建 .venv，不动系统 Python）…")
    try:
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    except Exception as e:
        print(f"创建虚拟环境失败：{e}")
        print("请先安装 Python 3.8+：https://www.python.org/downloads/")
        return 1

    py = _venv_python()
    if not py.is_file():
        print(f"虚拟环境创建异常，未找到 {py}")
        return 1

    print("安装依赖：fastapi uvicorn httpx cryptography …")
    r = subprocess.call([str(py), "-m", "pip", "install", "--quiet",
                        "fastapi", "uvicorn[standard]", "httpx", "cryptography"])
    if r != 0:
        print("依赖安装失败，请检查网络后重试。")
        return 1

    target = sys.argv[1:]
    print("依赖就绪，启动中…\n")
    return subprocess.call([str(py), str(HERE / target[0])] + target[1:])


if __name__ == "__main__":
    sys.exit(main())
