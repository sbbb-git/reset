#!/usr/bin/env python3
"""Le 33 Foch via Playwright — Plan B quand le widget Mindbody throttle l'IP CI.

Le scraper HTTP direct (le33foch_fetch.py) reçoit des 403/0-réponse depuis
les IPs GitHub Actions. On charge la page publique du planning dans un
Chromium headless, on attend le rendu du widget Mindbody, puis on extrait
les sessions du DOM.

Schéma de sortie identique à le33foch_fetch.fetch_all() pour rester
compatible avec le33foch_scrape.py — donc on monkey-patches la fonction.

Workflow : ré-exécution dans live-mindbody-le33foch.yml si le mode HTTP
échoue (fallback automatique).
"""
import asyncio
import datetime as dt
import re
import sys

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("❌ Playwright requis : pip install playwright && playwright install chromium",
          file=sys.stderr)
    sys.exit(2)

LIEU_FALLBACK = "Le 33 Foch"
PUBLIC_PAGE = "https://le33foch.fr/accueil/planning-et-reservations"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0 Safari/537.36")


def _clean(s):
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()


async def fetch_async():
    sessions = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(user_agent=UA, locale="fr-FR")
        page = await ctx.new_page()
        try:
            await page.goto(PUBLIC_PAGE, timeout=60000, wait_until="networkidle")
        except Exception as e:  # noqa: BLE001
            print(f"  Playwright nav échoué : {e}", file=sys.stderr)
            await browser.close()
            return sessions

        # Attendre que le widget Mindbody finisse de charger
        try:
            await page.wait_for_selector(".bw-session, .hc_starttime", timeout=30000)
        except Exception:  # noqa: BLE001
            print("  Widget Mindbody non détecté", file=sys.stderr)

        # Extraire toutes les sessions visibles
        rows = await page.evaluate("""
            () => {
              const sessions = [];
              document.querySelectorAll('.bw-session').forEach(el => {
                const id = el.getAttribute('data-bw-widget-id') || el.getAttribute('data-session-id') || '';
                const startEl = el.querySelector('.hc_starttime, time.hc_starttime');
                const endEl = el.querySelector('.hc_endtime, time.hc_endtime');
                const nameEl = el.querySelector('.bw-session__name');
                const staffEl = el.querySelector('.bw-session__staff');
                const locEl = el.querySelector('.bw-session__location');
                const cartEl = el.querySelector('.bw-widget__cart_button');
                if (!id || !startEl) return;
                sessions.push({
                  id, start: startEl.getAttribute('datetime') || '',
                  end: endEl ? endEl.getAttribute('datetime') || '' : '',
                  cours: (nameEl?.textContent || '').trim(),
                  coach: (staffEl?.textContent || '').trim(),
                  lieu: (locEl?.textContent || '').trim(),
                  cart: (cartEl?.textContent || '').trim(),
                });
              });
              return sessions;
            }
        """)
        await browser.close()

        seen = set()
        for s in rows:
            if not s.get("id") or s["id"] in seen:
                continue
            seen.add(s["id"])
            cours = _clean(s.get("cours"))
            cours = re.sub(r"^[A-Za-z]+\s*[-–]\s*", "", cours).strip() or cours
            sessions.append({
                "id": s["id"],
                "start": s["start"],
                "end": s["end"],
                "cours": cours,
                "coach": _clean(s.get("coach")),
                "lieu": _clean(s.get("lieu")) or LIEU_FALLBACK,
                "cart": _clean(s.get("cart")),
                "canceled": False,
                "widget": "playwright",
            })
    print(f"  Playwright : {len(sessions)} sessions récupérées", file=sys.stderr)
    return sessions


def fetch_all():
    """Compatible drop-in remplacement de le33foch_fetch.fetch_all()."""
    return asyncio.run(fetch_async())


if __name__ == "__main__":
    import json
    json.dump(fetch_all(), sys.stdout, ensure_ascii=False)
