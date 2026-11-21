"""Dang nhap Flow bang cookie JSON (xuat tu tien ich Cookie-Editor): nap cookie vao profile gflow.

Chay thu: python -m imgtool.cookie_login cookies.json [ten_profile]
"""
import json
import sys
from pathlib import Path

PROFILES = Path.home() / ".local/share/gflow-cli"
SAMESITE = {"no_restriction": "None", "lax": "Lax", "strict": "Strict"}


def to_playwright(c):
    out = {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c.get("path", "/"),
           "httpOnly": bool(c.get("httpOnly")), "secure": bool(c.get("secure")),
           "sameSite": SAMESITE.get((c.get("sameSite") or "").lower(), "Lax")}
    if c.get("expirationDate") and not c.get("session"):
        out["expires"] = c["expirationDate"]
    return out


def import_cookies(text, profile):
    """Nap cookie vao profile gflow `profile`. Tra ve so cookie da nap."""
    try:
        cookies = [to_playwright(c) for c in json.loads(text)]
    except (ValueError, KeyError, TypeError):
        raise ValueError("Cookie khong dung dinh dang JSON (mang cac cookie tu Cookie-Editor)")
    return load_into_profile(cookies, profile)


def load_into_profile(cookies, profile):
    """Ghi danh sach cookie (dang Playwright) vao profile gflow; tra ve so cookie."""
    if not any(c["name"] in ("SID", "__Secure-1PSID") for c in cookies):
        raise ValueError("Thieu cookie dang nhap Google (SID / __Secure-1PSID)")
    from playwright.sync_api import sync_playwright  # cai kem gflow-cli

    pdir = PROFILES / f"profile_{profile}"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / ".gflow_browser_strategy").write_text("chrome")  # gflow dung Chrome that cho profile nay
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(pdir), channel="chrome", headless=True,
            args=["--password-store=basic", "--disable-blink-features=AutomationControlled"])
        ctx.add_cookies(cookies)
        ctx.close()
    return len(cookies)


if __name__ == "__main__":
    n = import_cookies(Path(sys.argv[1]).read_text(), sys.argv[2] if len(sys.argv) > 2 else "cookietest")
    print("Da nap", n, "cookie")
