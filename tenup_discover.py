#!/usr/bin/env python3
"""FFT / Ten'Up : découverte des clubs padel affiliés FFT (lecture publique).

Le moteur "trouver un club" de tenup.fft.fr est public (pas de login pour la
liste des clubs et leur adresse). Le booking en revanche nécessite un compte
FFT — on n'y touche pas (scope CGU douteux).

Ce script récupère uniquement :
- la liste des clubs FFT avec activité padel
- adresse, code postal, ville, coordonnées GPS si exposées
- nombre de terrains déclarés (si présent dans la fiche)

Objectif : enrichir le store padel France avec un canal supplémentaire de
discovery (compléter Anybuddy / UrbanPadel / Doinsport / Playtomic avec les
clubs FFT non présents sur ces plateformes — souvent les clubs municipaux
ou associatifs).

Sortie : tenup_padel_clubs.json (liste, sans sessions/booking).

À intégrer dans padel_national_discover.py comme source #5 une fois validé.
"""
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Endpoint public "trouver un club" (filtré sur l'activité padel).
# Ces endpoints publics ne nécessitent pas de session FFT.
SEARCH_URL = "https://tenup.fft.fr/recherche/clubs"

# Codes postaux à parcourir : on cible les 14 métropoles couvertes par
# padel_national_discover.py pour rester aligné.
DEPTS_FR = [
    "75", "77", "78", "91", "92", "93", "94", "95",  # IDF
    "13", "06",                                       # PACA
    "33",                                             # Bordeaux
    "31",                                             # Toulouse
    "34",                                             # Montpellier
    "44",                                             # Nantes
    "35",                                             # Rennes
    "59", "62",                                       # Hauts-de-France
    "67",                                             # Strasbourg
    "69",                                             # Lyon
    "29", "35",                                       # Bretagne
    "76",                                             # Rouen
]


def http_get(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(2 ** attempt)
                continue
            return None
        except Exception:  # noqa: BLE001
            time.sleep(2 ** attempt)
    return None


def discover_dept(dept):
    """Cherche les clubs padel dans un département FR.
    Note : l'API/HTML exacte de tenup.fft.fr peut bouger ; ce script est un
    SCAFFOLD à compléter après inspection du DOM réel. Pour l'instant on log
    seulement les URLs construites pour validation manuelle.
    """
    # TODO inspecter la requête réelle (XHR JSON ?) via DevTools
    params = {"activite": "padel", "departement": dept}
    url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
    html = http_get(url)
    if not html:
        return []
    # placeholder : parsing à coder après inspection manuelle
    # on stocke juste un marker pour pouvoir lister les depts qu'on a tenté
    return [{"departement": dept, "url_recherche": url, "html_bytes": len(html)}]


def main():
    print("⚠️  SCAFFOLD : ce script est un point de départ. À compléter après",
          file=sys.stderr)
    print("    inspection manuelle du DOM/XHR de tenup.fft.fr (recherche club).",
          file=sys.stderr)
    print("    Booking : explicitement HORS scope (login + CGU douteuses).",
          file=sys.stderr)
    out = []
    for dept in sorted(set(DEPTS_FR)):
        rows = discover_dept(dept)
        print(f"  dept {dept} : {len(rows)} marker(s)")
        out.extend(rows)
        time.sleep(1)  # politesse FFT
    payload = {
        "checked_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "departments_scanned": sorted(set(DEPTS_FR)),
        "markers": out,
        "status": "scaffold_only",
    }
    with open("tenup_padel_clubs.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"→ tenup_padel_clubs.json ({len(out)} markers).")


if __name__ == "__main__":
    main()
