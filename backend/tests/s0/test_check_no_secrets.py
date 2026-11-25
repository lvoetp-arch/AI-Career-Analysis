import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "check_no_secrets.py"
spec = importlib.util.spec_from_file_location("check_no_secrets", SCRIPT)
cns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cns)

# 假資料在執行時才組字串，避免測試檔本身被檢查腳本擋下
FAKE_ANTHROPIC = "sk-ant-" + "api03-" + "A" * 30
FAKE_JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJ" + "yb2xlIjoic2VydmljZV9yb2xlIn0" + "." + "abcdefghijk123456"
FAKE_PHONE = "09" + "12" + "345678"
FAKE_PHONE_DASH = "09" + "12-345-" + "678"
FAKE_PHONE_INTL = "+886" + "-912-345-678"
FAKE_EMAIL = "alice" + "@" + "gmail.com"
FAKE_ID = "A" + "123456789"


@pytest.mark.parametrize("text", [
    f"key = '{FAKE_ANTHROPIC}'",
    f"token={FAKE_JWT}",
    "SUPABASE_SERVICE_KEY" + "=abcdefghijklmnop",
    "ANTHROPIC_API_KEY" + ': "abcdefghijklmnop"',
    "GEMINI_API_KEY" + "=abcdefghijklmnop",
    "AIza" + "Sy" + "A" * 33,
    "service_role" + "_key = abcdefghijklmnopqrstuvwxyz",
    f"電話 {FAKE_PHONE}", f"電話 {FAKE_PHONE_DASH}", f"tel {FAKE_PHONE_INTL}",
    f"mail {FAKE_EMAIL}", f"身分證 {FAKE_ID}",
])
def test_content_rules_block(text):
    assert cns.scan_text(text), text


@pytest.mark.parametrize("text", [
    "SUPABASE_SERVICE_KEY=",
    "service_role",
    "grant execute on function x to service_role;",
    "git@github.com:someone/repo.git",
    "user@example.com",
    "20261110090000_migration.sql",
    "@pytest.mark.skipif(True)",
    f"{FAKE_PHONE} " + "# check-no-secrets: allow",
])
def test_content_rules_allow_clean_text(text):
    assert cns.scan_text(text) == []


def test_path_rules():
    assert cns.check_path("test-resumes/a.txt")
    assert cns.check_path("x/test-resumes/a.txt")
    assert cns.check_path(".env") and cns.check_path("backend/.env.local")
    assert not cns.check_path(".env.example")
    assert cns.check_path("resume.pdf") and not cns.check_path("docs/spec.pdf")


def test_output_does_not_echo_matched_content():
    msgs = cns.scan_file("a.txt", f"x {FAKE_ANTHROPIC} {FAKE_PHONE}".encode())
    assert msgs and all(FAKE_ANTHROPIC not in m and FAKE_PHONE not in m for m in msgs)


@pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")
class TestGitIntegration:
    def _repo(self, tmp_path):
        def git(*a):
            subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        return git

    def _run(self, tmp_path, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=tmp_path, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    def test_blocks_fake_key_and_phone_in_staging_area(self, tmp_path):
        git = self._repo(tmp_path)
        (tmp_path / "notes.txt").write_text(f"{FAKE_ANTHROPIC}\n聯絡 {FAKE_PHONE}\n", encoding="utf-8")
        git("add", "notes.txt")
        r = self._run(tmp_path)
        assert r.returncode == 1
        assert "notes.txt:1" in r.stderr and "notes.txt:2" in r.stderr
        assert FAKE_ANTHROPIC not in r.stderr and FAKE_PHONE not in r.stderr

    def test_blocks_test_resumes_and_env_paths(self, tmp_path):
        git = self._repo(tmp_path)
        (tmp_path / "test-resumes").mkdir()
        (tmp_path / "test-resumes" / "a.txt").write_text("hello")
        (tmp_path / ".env").write_text("A=1")
        git("add", "-f", "test-resumes/a.txt", ".env")
        r = self._run(tmp_path)
        assert r.returncode == 1 and "test-resumes" in r.stderr and ".env" in r.stderr

    def test_clean_commit_passes(self, tmp_path):
        git = self._repo(tmp_path)
        (tmp_path / "a.py").write_text("print('hi')\n")
        git("add", "a.py")
        assert self._run(tmp_path).returncode == 0

    def test_checks_staged_version_not_working_tree(self, tmp_path):
        git = self._repo(tmp_path)
        f = tmp_path / "a.txt"
        f.write_text(FAKE_PHONE)
        git("add", "a.txt")
        f.write_text("clean now")  # 工作區改乾淨，但暫存的仍是髒的
        assert self._run(tmp_path).returncode == 1

    def test_all_mode_audits_tracked_files(self, tmp_path):
        git = self._repo(tmp_path)
        (tmp_path / "a.txt").write_text(FAKE_EMAIL)
        git("add", "a.txt")
        git("commit", "-qm", "x", "--no-verify")
        assert self._run(tmp_path).returncode == 0  # 暫存區是空的
        assert self._run(tmp_path, "--all").returncode == 1


# ---- 已追蹤檔案的內容（暫存區／HEAD），不是工作目錄 ----
def _git_out(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True).stdout


def tracked_findings(root):
    """掃描 git 已追蹤檔案「在暫存區裡的內容」（git ls-files -s 取得內容代號，再用 git cat-file 讀出）。
    工作目錄的本機修改（例如帶真實設定的 config.js）不會影響結果。回傳 [「路徑:行號 規則」]，不含命中的內容。"""
    entries = []
    for rec in _git_out(root, "ls-files", "-z", "-s").split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, sha, _stage = meta.split(b" ")
        if mode == b"160000":  # 子模組
            continue
        entries.append((path.decode("utf-8", errors="replace"), sha))
    if not entries:
        return []
    proc = subprocess.run(["git", "cat-file", "--batch"], cwd=root, capture_output=True, check=True,
                          input=b"\n".join(sha for _, sha in entries) + b"\n")
    out, pos, findings = proc.stdout, 0, []
    for rel, _ in entries:
        nl = out.index(b"\n", pos)
        _sha, _type, size = out[pos:nl].split(b" ")
        data = out[nl + 1: nl + 1 + int(size)]
        pos = nl + 1 + int(size) + 1
        if rel.lower().endswith(".pdf"):
            continue
        findings += cns.scan_file(rel, data)
    return findings


def test_repo_itself_is_clean():
    """git 已追蹤的檔案（以暫存區內容為準）不應命中自己的規則。
    只看已追蹤內容：被 .gitignore 排除的檔案（如 .env、config.local.js）與工作目錄的本機修改都不在範圍內。"""
    if shutil.which("git") is None:
        pytest.skip("需要 git")
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True)
    if r.returncode != 0:
        pytest.skip("不是 git repo")
    assert tracked_findings(ROOT) == []


# 真實樣式的值只在測試執行時組字串（測試檔本身不含會被規則命中的內容）
def _real_looking_config():
    return ('window.APP_CONFIG = {\n  SUPABASE_URL: "https://abcdefgh.supabase.co",\n  '
            + "SUPABASE_ANON_KEY" + ': "' + FAKE_JWT + '"\n};\n')


@pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")
class TestTrackedContentScan:
    PLACEHOLDER = 'window.APP_CONFIG = {\n  SUPABASE_URL: "",\n  SUPABASE_ANON_KEY: ""\n};\n'

    def _repo(self, tmp_path):
        def git(*a):
            subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        cfg = tmp_path / "config.js"
        cfg.write_text(self.PLACEHOLDER, encoding="utf-8")
        git("add", "config.js")
        git("commit", "-qm", "placeholder", "--no-verify")
        return git, cfg

    def test_real_looking_values_only_in_working_tree_do_not_fail(self, tmp_path):
        git, cfg = self._repo(tmp_path)
        cfg.write_text(_real_looking_config(), encoding="utf-8")  # 只改工作目錄，沒有 git add
        assert cns.scan_text(cfg.read_text(encoding="utf-8"))  # 前提：這份內容確實會命中規則
        assert tracked_findings(tmp_path) == []

    def test_real_looking_values_in_staging_area_fail(self, tmp_path):
        git, cfg = self._repo(tmp_path)
        cfg.write_text(_real_looking_config(), encoding="utf-8")
        git("add", "config.js")
        findings = tracked_findings(tmp_path)
        assert any(f.startswith("config.js:") for f in findings)
        assert all(FAKE_JWT not in f for f in findings)  # 輸出不回顯命中的內容

    def test_working_tree_cleanup_does_not_hide_staged_content(self, tmp_path):
        git, cfg = self._repo(tmp_path)
        cfg.write_text(_real_looking_config(), encoding="utf-8")
        git("add", "config.js")
        cfg.write_text(self.PLACEHOLDER, encoding="utf-8")  # 工作目錄改乾淨，暫存區仍是髒的
        assert tracked_findings(tmp_path)

    def test_untracked_ignored_local_config_is_out_of_scope(self, tmp_path):
        git, cfg = self._repo(tmp_path)
        (tmp_path / ".gitignore").write_text("config.local.js\n", encoding="utf-8")
        git("add", ".gitignore")
        (tmp_path / "config.local.js").write_text(_real_looking_config(), encoding="utf-8")
        assert tracked_findings(tmp_path) == []
