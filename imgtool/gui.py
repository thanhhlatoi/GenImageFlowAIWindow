"""Giao dien PySide6 kieu Auto Whisk: nhap prompt, chon ti le/so anh/thu muc, bam chay, xem trang thai tung dong.

Chay: python -m imgtool gui
"""
import os
import re
from datetime import datetime, timedelta, timezone
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .compose import render
from .cookie_login import import_cookies
from .gflow import find_gflow, prompt_results, run_gflow
from .paths import OUTPUT_ROOT
from .template import ensure_asset

TPL_ROOT = Path(__file__).resolve().parent.parent / "templates"
TEMPLATES = sorted(p.parent.name for p in TPL_ROOT.glob("*/template.json"))

RATIOS = {"Ngang 16:9": "16:9", "Doc 9:16": "9:16", "Vuong 1:1": "1:1"}

# Mau/kieu lay theo Auto Whisk Generator (chi giao dien, khong lay code)
STYLE = """
QWidget { background-color: #f4f7f6; font-family: "Segoe UI", sans-serif; font-size: 13px; color: #333; }
QLabel { background-color: transparent; }
QGroupBox { background-color: #fff; border-radius: 8px; margin-top: 22px; padding-top: 5px; border: 1px solid #e0e0e0; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 4px 10px;
  background-color: #34495e; color: white; border-radius: 4px; font-weight: bold; margin-left: 10px; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox { border: 1px solid #ccc; border-radius: 4px; padding: 5px; background: #fff; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #3498db; }
QPushButton { border-radius: 5px; padding: 6px 12px; font-weight: bold; color: white; border: none; background: #3498db; }
QPushButton:disabled { background: #bdc3c7; color: #7f8c8d; }
QTableWidget { background: white; border: 1px solid #e0e0e0; gridline-color: #f0f0f0; }
QHeaderView::section { background: #ecf0f1; padding: 6px; border: none; border-bottom: 2px solid #dcdcdc; font-weight: bold; color: #2c3e50; }
QProgressBar { border: 1px solid #ccc; border-radius: 4px; text-align: center; background: #fff; }
QProgressBar::chunk { background: #27ae60; }
"""


def color(btn, bg):
    btn.setStyleSheet(f"QPushButton {{ background: {bg}; }} QPushButton:disabled {{ background: #bdc3c7; color: #7f8c8d; }}")


class AuthJob(QThread):
    """Chay `gflow auth login` (mo Chrome de dang nhap) hoac `auth status`; tra ve email hoac None."""
    done = Signal(object)

    def __init__(self, login):
        super().__init__()
        self.login = login

    def run(self):
        try:
            g = find_gflow()
            env = {**os.environ, "GFLOW_CLI_UPDATE_CHECK": "0"}
            if self.login:  # Chrome that, hien len de nguoi dung dang nhap Google
                # Profile moi sach: neu con cookie cu, gflow tuong da dang nhap va dong Chrome ngay
                subprocess.run([g, "auth", "logout", "--profile", "web", "-y"], env=env, capture_output=True)
                r = subprocess.run([g, "auth", "login", "--browser", "chrome", "--profile", "web"], env=env)
                if r.returncode == 0:  # chi doi tai khoan mac dinh khi dang nhap thanh cong
                    subprocess.run([g, "auth", "use", "web"], env=env, capture_output=True)
            r = subprocess.run([g, "auth", "status"], env=env, capture_output=True, text=True)
            m = re.search(r"verified as (\S+?)\.?\s*$", r.stdout, re.M)
            self.done.emit(m.group(1) if m else None)
        except (SystemExit, OSError):
            self.done.emit(None)


class CookieJob(QThread):
    """Nap cookie JSON vao profile gflow 'cookie', dat lam mac dinh, roi tra ve email (hoac loi)."""
    done = Signal(object, str)

    def __init__(self, text):
        super().__init__()
        self.text = text

    def run(self):
        try:
            import_cookies(self.text, "cookie")
            subprocess.run([find_gflow(), "auth", "use", "cookie"], capture_output=True)
            self.done.emit(True, "")
        except (ValueError, SystemExit, OSError) as e:
            self.done.emit(False, str(e))
        except Exception as e:  # playwright/Chrome loi khi mo profile
            self.done.emit(False, f"Khong nap duoc cookie: {e}")


class Worker(QThread):
    row = Signal(int, str)   # (dong, trang thai)
    images = Signal(int, list)   # (dong, [duong dan anh])

    def __init__(self, jobs, out, count, aspect, template=None, batch=3):
        super().__init__()
        self.jobs, self.out, self.count, self.aspect = jobs, out, count, aspect
        self.template = template  # None = tao anh tu prompt; ten = dung titles lam anh theo template
        self.batch = batch
        self.paused = False
        self.stopped = False

    def run(self):
        # Prompt: gui `batch` prompt/lan trong 1 lan goi gflow (1 Chrome, Flow tao cung luc). Template: tung dong.
        size = 1 if self.template else self.batch
        for k in range(0, len(self.jobs), size):
            while self.paused and not self.stopped:
                self.msleep(200)
            if self.stopped:
                return
            chunk = self.jobs[k:k + size]
            for i, _ in chunk:
                self.row.emit(i, "Dang tao...")
            try:
                if self.template:
                    status, path = self.make(*chunk[0])
                    self.images.emit(chunk[0][0], [path])
                    self.row.emit(chunk[0][0], status)
                    continue
                since = (datetime.now(timezone.utc) - timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
                run_gflow([p for _, p in chunk], self.out, self.count, self.aspect)
                res = prompt_results([p for _, p in chunk], since)  # ket qua rieng tung prompt
                for i, p in chunk:
                    ok, err, files = res.get(p, (False, "khong thay ket qua", []))
                    self.images.emit(i, files[:4])
                    self.row.emit(i, f"Xong ({len(files)} anh)" if ok and files else f"Loi: {err or 'khong co anh'}")
            except SystemExit as e:  # run_gflow thoat khi thieu gflow
                for i, _ in chunk:
                    self.row.emit(i, f"Loi: {e}")
                return
            except Exception as e:  # vd tieu de qua dai: loi 1 dong khong dung ca lo
                for i, _ in chunk:
                    self.row.emit(i, f"Loi: {e}")

    def make(self, i, title):
        tpl = TPL_ROOT / self.template
        ensure_asset(tpl, "avatar.jpg", "avatar_prompt", "1:1", False)
        ensure_asset(tpl, "bg.jpg", "bg_prompt", "16:9", False)
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:40].strip("-")
        out = Path(self.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{i + 1:02d}_{slug}.png"
        render(tpl, title, path)
        return "Xong (1 anh)", str(path)


class Win(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GenImageFlow - Tao anh Google Flow")
        self.resize(900, 700)
        self.worker = None
        self.logged_in = False

        self.ratio = QComboBox()
        self.ratio.addItems(RATIOS)
        self.num = QSpinBox()
        self.num.setRange(1, 4)
        self.batch = QSpinBox()
        self.batch.setRange(1, 10)
        self.batch.setValue(3)
        self.out = QLineEdit(str(OUTPUT_ROOT / "genImage"))
        pick = QPushButton("...")
        pick.clicked.connect(self.pick_folder)
        self.prompts = QPlainTextEdit()
        self.prompts.setMaximumHeight(130)
        self.prompts.setPlaceholderText("Moi dong 1 prompt (hoac 1 tieu de neu dung template)")
        self.mode = QComboBox()
        self.mode.addItems(["Tao anh tu prompt", *TEMPLATES])
        self.mode.currentIndexChanged.connect(self.on_mode)

        cfg = QGroupBox("Cau hinh && Prompts")
        form = QVBoxLayout(cfg)
        r1 = QHBoxLayout()
        for w in (QLabel("Kieu:"), self.mode, QLabel("Ti le:"), self.ratio, QLabel("So anh/prompt:"), self.num, QLabel("Chay cung luc:"), self.batch):
            r1.addWidget(w)
        r1.addStretch()
        r2 = QHBoxLayout()
        for w in (QLabel("Thu muc luu:"), self.out, pick):
            r2.addWidget(w)
        for lay in (r1, r2):
            form.addLayout(lay)
        form.addWidget(QLabel("Danh sach prompt (moi dong 1 prompt):"))
        form.addWidget(self.prompts)

        self.start = QPushButton("BAT DAU")
        self.pause = QPushButton("Tam dung")
        self.folder = QPushButton("Mo thu muc")
        self.acct = QLabel("Dang kiem tra tai khoan Google...")
        self.login = QPushButton("Dang nhap Google")
        color(self.login, "#e67e22")
        self.login.setEnabled(False)
        self.login.clicked.connect(lambda: self.check_auth(True))
        self.cookie = QPushButton("Dan cookie")
        color(self.cookie, "#8e44ad")
        self.cookie.clicked.connect(self.paste_cookie)
        acct = QHBoxLayout()
        acct.addWidget(self.acct, 1)
        acct.addWidget(self.cookie)
        acct.addWidget(self.login)
        color(self.start, "#27ae60")
        color(self.pause, "#f39c12")
        color(self.folder, "#34495e")
        self.pause.setEnabled(False)
        self.start.clicked.connect(self.on_start)
        self.pause.clicked.connect(self.on_pause)
        self.folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.out.text())))
        btns = QHBoxLayout()
        for b in (self.start, self.pause, self.folder):
            btns.addWidget(b)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Prompt", "Trang thai", "Anh (bam de mo)"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setDefaultSectionSize(100)
        self.table.setWordWrap(True)
        self.bar = QProgressBar()

        lay = QVBoxLayout(self)
        for w in (cfg,):
            lay.addWidget(w)
        lay.addLayout(acct)
        lay.addLayout(btns)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.bar)

    def check_auth(self, login=False):
        self.login.setEnabled(False)
        self.acct.setText("Cua so Chrome dang mo: hay dang nhap Google roi cho..." if login else "Dang kiem tra tai khoan Google...")
        self.auth = AuthJob(login)
        self.auth.done.connect(self.on_auth)
        self.auth.start()

    def paste_cookie(self):
        d = QDialog(self)
        d.setWindowTitle("Dang nhap bang cookie")
        box = QPlainTextEdit()
        box.setPlaceholderText("Dan cookie JSON (xuat tu Cookie-Editor khi dang o flow.google.com)")
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        lay = QVBoxLayout(d)
        lay.addWidget(box)
        lay.addWidget(bb)
        d.resize(600, 400)
        if not d.exec() or not box.toPlainText().strip():
            return
        self.login.setEnabled(False)
        self.cookie.setEnabled(False)
        self.acct.setText("Dang nap cookie...")
        self.cj = CookieJob(box.toPlainText())
        self.cj.done.connect(self.on_cookie)
        self.cj.start()

    def on_cookie(self, ok, err):
        self.cookie.setEnabled(True)
        if ok:
            self.check_auth()
        else:
            self.login.setEnabled(True)
            self.acct.setText("Nap cookie that bai")
            QMessageBox.warning(self, "Cookie loi", err)

    def on_auth(self, email):
        self.logged_in = bool(email)
        self.acct.setText(f"Da dang nhap: {email}" if email else "CHUA dang nhap Google - bam nut ben phai")
        self.acct.setStyleSheet(f"color: {'#27ae60' if email else '#c0392b'}; font-weight: bold;")
        self.login.setText("Doi tai khoan" if email else "Dang nhap Google")
        self.login.setEnabled(True)

    def on_mode(self):
        # Template -> thu muc rieng theo ten template (giong lenh make); prompt -> genImage
        sub = self.mode.currentText() if self.mode.currentIndex() else "genImage"
        self.out.setText(str(OUTPUT_ROOT / sub))
        self.num.setEnabled(not self.mode.currentIndex())
        self.batch.setEnabled(not self.mode.currentIndex())
        self.ratio.setEnabled(not self.mode.currentIndex())

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Chon thu muc luu", self.out.text())
        if d:
            self.out.setText(d)

    def on_start(self):
        lines = [l.strip() for l in self.prompts.toPlainText().splitlines()]
        lines = [l for l in lines if l and not l.startswith("#")]
        if not lines:
            return QMessageBox.warning(self, "Thieu prompt", "Nhap it nhat 1 prompt!")
        if not self.mode.currentIndex() and not self.logged_in:
            return QMessageBox.warning(self, "Chua dang nhap", "Bam 'Dang nhap Google' truoc khi tao anh.")
        self.table.setRowCount(0)  # xoa ket qua + anh cu
        self.table.setRowCount(len(lines))
        for i, p in enumerate(lines):
            self.table.setItem(i, 0, QTableWidgetItem(p))
            self.table.setItem(i, 1, QTableWidgetItem("Dang cho"))
        self.bar.setRange(0, len(lines))
        self.bar.setValue(0)
        self.worker = Worker(list(enumerate(lines)), self.out.text(), self.num.value(), RATIOS[self.ratio.currentText()],
                             self.mode.currentText() if self.mode.currentIndex() else None,
                             self.batch.value())
        self.worker.row.connect(self.on_row)
        self.worker.images.connect(self.on_images)
        self.worker.finished.connect(lambda: self.set_running(False))
        self.set_running(True)
        self.worker.start()

    def on_pause(self):
        w = self.worker
        w.paused = not w.paused
        self.pause.setText("Tiep tuc" if w.paused else "Tam dung")

    def on_images(self, i, files):
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(4, 4, 4, 4)
        for f in files:
            pm = QPixmap(f)
            if pm.isNull():
                continue
            b = QPushButton()
            b.setFlat(True)
            b.setIcon(QIcon(pm))
            b.setIconSize(pm.scaled(120, 90, Qt.KeepAspectRatio).size())
            b.setToolTip(f)
            b.setStyleSheet("QPushButton { background: transparent; }")
            b.clicked.connect(lambda _=False, f=f: QDesktopServices.openUrl(QUrl.fromLocalFile(f)))
            lay.addWidget(b)
        lay.addStretch()
        self.table.setCellWidget(i, 2, cell)

    def on_row(self, i, status):
        it = QTableWidgetItem(status)
        if status.startswith("Loi"):  # de thay ngay prompt nao loi
            it.setForeground(QColor("#c0392b"))
            it.setBackground(QColor("#fdecea"))
            it.setToolTip(status)
        elif status.startswith("Xong"):
            it.setForeground(QColor("#27ae60"))
        self.table.setItem(i, 1, it)
        if not status.startswith("Dang"):
            self.bar.setValue(self.bar.value() + 1)

    def set_running(self, on):
        self.start.setEnabled(not on)
        self.pause.setEnabled(on)
        if not on:
            self.pause.setText("Tam dung")

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            self.worker.stopped = True
            self.worker.wait(3000)
        e.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    w = Win()
    w.show()
    w.check_auth()
    sys.exit(app.exec())
