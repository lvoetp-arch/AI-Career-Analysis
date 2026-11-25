"""Serve a two-version UI preview using only local fixtures."""
import argparse
import html
import json
import re
import shutil
import subprocess
import tempfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "72a5302"
RUN = "11111111-1111-4111-8111-111111111111"
PAGES = (
    ("首頁", "index.html"), ("上傳", "upload.html"), ("確認履歷", "review.html"),
    ("技能自評", "assess.html"), ("分析等待", f"analyzing.html?career_input_id={RUN}"),
    ("正式報告", f"report.html?id={RUN}"), ("範例報告", "sample_report.html"),
    ("歷史紀錄", "history.html"), ("登入", "login.html?next=upload.html"),
    ("註冊", "register.html"), ("隱私", "privacy.html"), ("帳號設定", "settings.html"),
)


class PreviewHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "connect-src 'self'; form-action 'self'; base-uri 'self'")
        super().end_headers()


def add_preview(directory, version, fixture, revision):
    other = "original" if version == "proposed" else "proposed"
    label = "新版預覽" if version == "proposed" else "目前版對照"
    next_label = "看目前版" if version == "proposed" else "看新版"
    (directory / "preview-fixtures.js").write_text(
        "window.previewProfile = " + json.dumps(fixture, ensure_ascii=False) + ";\n"
        + (ROOT / "preview/fixtures.js").read_text(encoding="utf-8"), encoding="utf-8",
    )
    links = " ".join(f'<a href="{html.escape(url)}">{name}</a>' for name, url in PAGES)
    for page in directory.glob("*.html"):
        source = page.read_text(encoding="utf-8")
        source = re.sub(r'<script src="(?:js/(?:config|auth|api)\.js|vendor/[^"]+)"></script>\s*', "", source)
        source = source.replace('<script src="js/ui.js"></script>',
            '<script src="preview-fixtures.js"></script><script src="js/ui.js"></script>')
        ribbon = (f'<aside class="preview-ribbon"><div><strong>{label}</strong> · 模擬資料 · <code>{revision}</code>'
                  f' <a href="../">全站預覽目錄</a> <a id="preview-switch" href="../{other}/{page.name}">{next_label}</a></div>'
                  f'<nav aria-label="預覽頁面">{links}</nav></aside>')
        source = re.sub(r"(<body[^>]*>)", lambda match: match[0] + ribbon, source, count=1)
        source = source.replace("</head>", """<style>
.preview-ribbon {padding:8px 16px;background:#222;color:#fff;font:14px/1.5 sans-serif}
.preview-ribbon > div,.preview-ribbon nav {display:flex;gap:6px 14px;flex-wrap:wrap}
.preview-ribbon nav {padding-top:4px}
.preview-ribbon a {color:#fff}
@media print {.preview-ribbon {display:none}}
</style></head>""")
        page.write_text(source, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default=8767, type=int)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    fixture = json.loads((ROOT / "backend/tests/fixtures/sample_resume_profile.fixture.json").read_text(encoding="utf-8"))
    fixture["portfolio"][0]["url"] = None
    fixture["portfolio"][0]["url_truncated"] = True
    fixture["missing_flags"] = [{
        "field_path": "portfolio[0].url", "reason": "truncated", "message": "請核對作品集網址。"
    }]
    revision = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True,
    ).strip()
    with tempfile.TemporaryDirectory(prefix="career-ui-preview-") as tmp:
        preview_root = Path(tmp)
        shutil.copytree(ROOT / "frontend", preview_root / "proposed")
        original = preview_root / "original"
        original.mkdir()
        archive = subprocess.check_output(["git", "archive", f"{BASELINE}:frontend"], cwd=ROOT)
        subprocess.run(["tar", "-x", "-C", str(original)], input=archive, check=True)
        for version in ("proposed", "original"):
            add_preview(preview_root / version, version, fixture, revision if version == "proposed" else BASELINE)
        rows = "".join(f"<tr><th scope='row'>{name}</th><td><a href='proposed/{html.escape(url)}'>新版</a></td>"
                       f"<td><a href='original/{html.escape(url)}'>目前版</a></td></tr>" for name, url in PAGES)
        (preview_root / "index.html").write_text(f"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>職話直說｜全站 UI 預覽</title><style>
*{{box-sizing:border-box}}body{{background:#f5f2e8;color:#201a14;font:16px/1.6 system-ui;margin:0;padding:24px}}
main{{max-width:670px;margin:auto}}h1{{line-height:1.2}}table{{width:100%;background:#fffef9;border-collapse:collapse}}
th,td{{text-align:left;padding:9px 12px;border-bottom:1px solid #ddd4c0}}a{{color:#842511}}
</style><main><h1>職話直說｜全站 UI 預覽</h1>
<p>兩版都使用同一組模擬資料。可從任一頁切換版本。</p>
<table><thead><tr><th scope="col">頁面</th><th scope="col">新版</th><th scope="col">目前版</th></tr></thead>
<tbody>{rows}</tbody></table></main></html>""", encoding="utf-8")
        server = ThreadingHTTPServer((args.host, args.port), partial(PreviewHandler, directory=tmp))
        print(f"Preview available on http://{args.host}:{args.port} (proposed {revision})", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.server_close()


if __name__ == "__main__":
    main()
