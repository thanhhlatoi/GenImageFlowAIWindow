"""Chay gflow nhung tu bam 'Khong, cam on' khi Flow hien hop thoai khuyen mai che nut (vd tang 20 tin dung).

Hop thoai nay hien lai moi lan vao Flow den khi nhan thuong, nen gflow bi nghen o nut "Du an moi".
Dung `page.add_locator_handler` cua Playwright: moi lan thao tac bi che, no tu bam tat roi lam tiep.
Dung thay cho lenh `gflow`: python -m imgtool.gflow_run image t2i ...
"""
import asyncio
import re
import sys

from playwright.async_api import BrowserType

DECLINE = re.compile(r"^(Không, cảm ơn|No,? thanks)$", re.I)
_orig = BrowserType.launch_persistent_context


async def _dismiss(loc):
    await loc.click()


async def _watch(page):
    await page.add_locator_handler(page.get_by_role("button", name=DECLINE), _dismiss)


async def _launch(self, *a, **k):
    ctx = await _orig(self, *a, **k)
    for p in ctx.pages:
        await _watch(p)
    ctx.on("page", lambda p: asyncio.ensure_future(_watch(p)))
    return ctx


BrowserType.launch_persistent_context = _launch

if __name__ == "__main__":
    from gflow_cli.cli import main
    sys.argv[0] = "gflow"
    sys.exit(main())
