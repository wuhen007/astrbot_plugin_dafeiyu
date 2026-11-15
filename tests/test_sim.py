"""终端模拟器端到端测试：直接跑 sim.py 验证两种结局。"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(args, tmp_path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(ROOT / "sim.py"), *args, "--data", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env,
    )


def test_auto_graduation(tmp_path):
    out = _run(["--auto"], tmp_path)
    assert out.returncode == 0, out.stderr
    assert "出栏大典" in out.stdout
    assert "评级" in out.stdout


def test_auto_neglect_runaway(tmp_path):
    out = _run(["--auto-neglect"], tmp_path)
    assert out.returncode == 0, out.stderr
    assert "不辞而别" in out.stdout
    assert "评级 C" in out.stdout
