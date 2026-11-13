"""Render the one-page sheets in docs/ to PDF (A4):

- docs/cheatsheet.html   -> docs/PingPong-cheat-sheet.pdf      (operators)
- docs/captain-chart.html -> docs/PingPong-captain-chart.pdf   (master PC)

Needs Playwright with Chromium (pip install playwright; playwright install
chromium). Run after changing a sheet: python tools/make_cheatsheet_pdf.py
"""

import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHEETS = [
    ("cheatsheet.html", "PingPong-cheat-sheet.pdf"),
    ("captain-chart.html", "PingPong-captain-chart.pdf"),
]


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for src, out in SHEETS:
            page.goto("file://" + os.path.join(ROOT, "docs", src))
            page.pdf(path=os.path.join(ROOT, "docs", out), format="A4",
                     print_background=True, prefer_css_page_size=True)
            print("written", out)
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
