"""S5：自評頁必須等登入就緒才呼叫 API。實際測試在 assess_auth_gate.test.js（node + jsdom）；
沒有 node 或 jsdom 時略過（pytest -rs 會顯示原因），靜態版本的檢查仍在 test_s5_frontend.py。"""
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def test_assess_waits_for_login_before_any_request():
    node = shutil.which("node")
    if not node:
        pytest.skip("找不到 node；安裝方式見 docs/handoff/S5.md 第 6 節")
    probe = subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if probe.returncode != 0:
        pytest.skip("找不到 jsdom；安裝方式見 docs/handoff/S5.md 第 6 節")
    r = subprocess.run([node, str(HERE / "assess_auth_gate.test.js")], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
