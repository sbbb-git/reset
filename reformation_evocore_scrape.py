#!/usr/bin/env python3
"""Reformation Pilates + Evocore Lagree : scraping via Playwright.

Ces 2 marques utilisent un widget Mindbody SPA (rendu côté client en JS),
non scrapable par requêtes HTTP simples comme banote/dna/le33foch.
On utilise Playwright headless pour piloter un navigateur réel,
attendre le rendu du planning, puis parser le DOM.

Workflow : reformation-evocore.yml, schedule quotidien (~lourd à exécuter,
pas la peine /30min, le statut Mindbody ne change presque pas dans la journée).

Sortie :
- reformation_data.json (statut figé par séance)
- evocore_data.json
- HTML reformation.html / evocore.html (templates calqués sur le pattern banote)

Notes :
- timeout long (60s) car SPA + cold start
- on capture le statut "complet"/"des places" comme banote
- pas de retry agressif (Mindbody throttle facilement)
"""
import asyncio
import datetime as dt
import json
import os
import sys

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("❌ Playwright non installé. pip install playwright && playwright install chromium",
          file=sys.stderr)
    sys.exit(2)

JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

# Configs des 2 marques. Url widget public Mindbody (ou page locale qui l'embed).
BRANDS = [
    {
        "name": "reformation",
        "url": "https://www.reformationpilates.fr/planning",
        "store": "reformation_data.json",
        "cap_default": 12,           # capacité reformer estimée
        "selector_wait": "[data-mindbody]",  # à ajuster après inspection live
    },
    {
        "name": "evocore",
        "url": "https://www.evocore.fr/planning",
        "store": "evocore_data.json",
        "cap_default": 10,           # capacité Lagree estimée
        "selector_wait": "[data-mindbody]",
    },
]


async def scrape_brand(page, cfg):
    """Charge la page widget, attend le SPA, retourne les séances parsées."""
    print(f"→ {cfg['name']} : {cfg['url']}")
    try:
        await page.goto(cfg["url"], timeout=60000, wait_until="networkidle")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ navigation échouée : {e}", file=sys.stderr)
        return []

    # Attendre que le widget Mindbody charge ses cellules
    try:
        await page.wait_for_selector(cfg["selector_wait"], timeout=20000)
    except Exception:  # noqa: BLE001
        print(f"  ⚠️ widget pas détecté (selector {cfg['selector_wait']})", file=sys.stderr)

    # Heuristique : on cherche les boutons "Book"/"Join Waitlist"/"Full"
    # à ajuster après inspection live du DOM réel
    rows = await page.evaluate("""
        () => {
          const out = [];
          const cells = document.querySelectorAll('[data-mb-class], .hc-class, .class-tile');
          cells.forEach(el => {
            const t = (el.textContent || '').trim();
            const time_m = t.match(/(\\d{1,2}):(\\d{2})/);
            const dateAttr = el.getAttribute('data-date') || el.dataset.date;
            const btn = el.querySelector('button, a.book');
            const btnText = (btn ? btn.textContent : '').toLowerCase();
            let statut = 'inconnu';
            if (/waitlist|complet|full/.test(btnText)) statut = 'complet';
            else if (/book|reserver|inscription/.test(btnText)) statut = 'disponible';
            out.push({
              date: dateAttr || null,
              heure: time_m ? time_m[1].padStart(2,'0')+':'+time_m[2] : null,
              cours: (el.querySelector('.class-name')?.textContent || '').trim(),
              coach: (el.querySelector('.instructor')?.textContent || '').trim(),
              statut,
            });
          });
          return out;
        }
    """)

    print(f"  ← {len(rows)} séances trouvées")
    return rows


async def run():
    now = dt.datetime.now()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            locale="fr-FR",
        )
        page = await ctx.new_page()
        for cfg in BRANDS:
            try:
                rows = await scrape_brand(page, cfg)
            except Exception as e:  # noqa: BLE001
                print(f"❌ {cfg['name']} : {e}", file=sys.stderr)
                continue
            if not rows:
                continue
            # Charge l'existant et merge par clé (date|heure|cours)
            existing = {}
            if os.path.exists(cfg["store"]):
                try:
                    existing = json.load(open(cfg["store"], encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    existing = {}
            for r in rows:
                if not r.get("date") or not r.get("heure"):
                    continue
                d_obj = dt.date.fromisoformat(r["date"])
                r["jour"] = JOURS_FR[d_obj.weekday()]
                key = f"{r['date']}|{r['heure']}|{(r.get('cours') or '').strip()}"
                # Verrouille le statut si la séance approche (15 min avant)
                start = dt.datetime.fromisoformat(f"{r['date']}T{r['heure']}:00")
                r["finie"] = now >= start
                r["releve"] = now.strftime("%Y-%m-%d %H:%M")
                r["capacite"] = cfg["cap_default"]
                r["presents"] = cfg["cap_default"] if r.get("statut") == "complet" else None
                existing[key] = r
            with open(cfg["store"], "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, separators=(",", ":"))
            print(f"  💾 {cfg['store']} : {len(existing)} séances totales en base")

        await browser.close()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
