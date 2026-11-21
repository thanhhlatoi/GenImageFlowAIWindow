"""Goi gflow-cli (khong chinh thuc) de tao anh tren Google Flow."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def find_gflow():
    """Tim lenh gflow: uu tien trong cung venv dang chay script."""
    local = Path(sys.executable).parent / "gflow"
    if local.exists():
        return str(local)
    found = shutil.which("gflow")
    if found:
        return found
    sys.exit("Khong tim thay lenh 'gflow'. Hay kich hoat venv: source .venv/bin/activate")


def list_images(root):
    images_dir = Path(root) / "images"
    if not images_dir.exists():
        return set()
    return {p for p in images_dir.rglob("*") if p.suffix.lower() in IMG_EXT}


def run_gflow(prompts, out_dir, count=1, aspect=None, model=None, jitter=None):
    """Goi `gflow image t2i` MOT LAN cho tat ca prompt (chi mo browser 1 lan).

    Tra ve (exit_code, danh sach file anh moi).
    """
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    find_gflow()  # bao loi som neu chua kich hoat venv
    cmd = [sys.executable, "-m", "imgtool.gflow_run", "image", "t2i", *prompts, "-n", str(count), "--continue-on-error"]
    # Chay Chrome tren man hinh ao (Xvfb) de khong hien len; dat IMGTOOL_SHOW=1 de xem cua so.
    # Khong dung headless that: reCAPTCHA tu choi (403), nen van la Chrome co giao dien.
    if shutil.which("xvfb-run") and not os.environ.get("IMGTOOL_SHOW"):
        cmd = ["xvfb-run", "-a", *cmd]
    if aspect:
        cmd += ["--aspect", aspect]
    if model:
        cmd += ["--model", model]
    if jitter:
        cmd += ["--jitter", jitter]

    root = str(Path(__file__).resolve().parent.parent)  # de `-m imgtool.gflow_run` chay duoc tu moi thu muc
    env = {**os.environ, "GFLOW_CLI_OUTPUT_DIR": str(out_dir), "PYTHONPATH": root}
    before = list_images(out_dir)
    # Khong capture de thay tien trinh/loi cua gflow truc tiep tren man hinh
    code = subprocess.run(cmd, env=env).returncode
    return code, sorted(list_images(out_dir) - before)


def prompt_results(prompts, since):
    """Doc ket qua tung prompt tu so lieu cua gflow (gflow.db): {prompt: (ok, loi, [file anh])}.

    `since` = chuoi UTC dang `2026-11-21T05:50:42Z` (luc bat dau chay). Prompt khong co dong nao -> khong co khoa.
    """
    import sqlite3

    db = Path.home() / ".local/share/gflow-cli/gflow.db"
    if not db.exists():
        return {}
    res = {}
    with sqlite3.connect(db) as c:
        for pr in set(prompts):
            rows = c.execute(
                "select id, status, error_detail from operations "
                "where command = 'image t2i' and prompt = ? and started_at >= ? order by started_at",
                (pr, since)).fetchall()
            for op, status, err in rows:
                files = [r[0] for r in c.execute(
                    "select l.path from operation_assets oa join local_files l on l.asset_id = oa.asset_id "
                    "where oa.operation_id = ? order by oa.position", (op,))]
                ok0, err0, f0 = res.get(pr, (True, "", []))
                res[pr] = (ok0 and status == "succeeded", err0 or err or ("" if status == "succeeded" else status), f0 + files)
    return res
