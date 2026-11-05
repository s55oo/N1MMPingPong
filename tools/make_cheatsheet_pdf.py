"""Render docs/cheatsheet.html to docs/PingPong-cheat-sheet.pdf (A4).

Needs Playwright with Chromium (pip install playwright; playwright install
chromium). Run after changing the cheat sheet: python tools/make_cheatsheet_pdf.py
"""

import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs", "cheatsheet.html")
OUT = os.path.join(ROOT, "docs", "PingPong-cheat-sheet.pdf")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("file://" + SRC)
        page.pdf(path=OUT, format="A4", print_background=True, prefer_css_page_size=True)
        browser.close()
    print("written", OUT)


if __name__ == "__main__":
    sys.exit(main())
