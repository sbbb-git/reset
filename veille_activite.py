#!/usr/bin/env python3
"""Classe les lieux non suivis de la veille par activité RÉELLE (bsport).

veille_lieux_discover.py dit QUI existe ; ce script dit QUI COMPTE : pour
chaque company bsport d'Île-de-France qui publie des créneaux et que l'on ne
scrape pas, il lit les 30 derniers jours (séances, réservations validées,
capacité) et en déduit un classement. Sortie : veille_activite.json, lue par
l'onglet « Veille » de l'Observatoire.

Corrige aussi l'angle mort de la veille : une salle ajoutée à une company
DÉJÀ scrapée (nouveau studio Le Cercle, AthletX…) y apparaît « non suivie »
alors qu'elle l'est. On croise donc avec toutes les companies réellement
scrapées (configs des scrapers + catalogues d'extensions).
"""
import collections
import datetime as dt
import glob
import json
import re
import sys

import bsport_scrape as b

ENTREE = "veille_nouveaux_lieux.json"
ETAT = "veille_lieux_etat.json"
SORTIE = "veille_activite.json"
TOP = 250


def companies_suivies():
    ids = set()
    for f in glob.glob("*_scrape.py"):
        s = open(f, encoding="utf-8").read()
        for m in re.finditer(r'"compan(?:y|ies)"\s*:\s*(\[[^\]]*\]|\d+)', s):
            ids |= set(re.findall(r"\d+", m.group(1)))
    for f in glob.glob("*_extension_resolved.json"):
        for v in json.load(open(f, encoding="utf-8")).values():
            if isinstance(v, dict) and v.get("company_id") and v.get("status") != "blocked":
                ids |= set(re.findall(r"\d+", str(v["company_id"])))
    return ids


def secteur(acts):
    t = " ".join(a.lower() for a, _ in acts)
    if re.search(r"pole|danse|dance|hip hop|jazz|ballet|bachata|heels|salsa|afro|dancehall|contempora", t):
        return "Danse"
    if re.search(r"natation|aqua|bébé|enfant|dès \d|apprentissage|dessin|peinture|modelage|tournage|massage|soin|gong|bain sonore|sound bath", t):
        return "Hors périmètre"
    if re.search(r"pilates|reformer|lagree|barre", t):
        return "Pilates"
    if re.search(r"yoga|vinyasa|hatha|yin|ashtanga|kundalini|méditation", t):
        return "Yoga"
    return "Fitness"


def main():
    v = json.load(open(ENTREE, encoding="utf-8"))
    etat = json.load(open(ETAT, encoding="utf-8"))
    lieux = etat.get("lieux", etat)
    salles = collections.defaultdict(set)
    for x in (lieux.values() if isinstance(lieux, dict) else lieux):
        salles[str(x.get("company_id"))].add(x.get("nom") or "")
    suivies = companies_suivies()
    today = dt.date.today()
    d0, d1 = today - dt.timedelta(days=30), today - dt.timedelta(days=1)
    out, deja = [], 0
    for x in v.get("enseignes", []):
        co = str(x.get("company_id"))
        if str(x.get("creneaux")) != "True":
            continue
        if co in suivies:
            deja += 1
            continue
        try:
            o = [y for y in b.fetch_offers(int(co), d0, d1)
                 if (y.get("date_start") or "")[:10] < today.isoformat()]
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ company {co} : {str(e)[:80]}", file=sys.stderr)
            continue
        res = sum(y.get("validated_booking_count") or 0 for y in o)
        if not res:
            continue
        cap = sum(y.get("effectif") or 0 for y in o if 0 < (y.get("effectif") or 0) < 100)
        acts = collections.Counter((y.get("activity_name") or "").strip() for y in o).most_common(6)
        out.append({
            "company_id": co, "enseigne": x.get("enseigne"), "ville": x.get("ville"),
            "cp": x.get("cp"), "premier_vu": x.get("premier_vu"),
            "salles": sorted(s for s in salles.get(co, ()) if s)[:8],
            "secteur": secteur(acts), "seances_30j": len(o), "resa_30j": res,
            "remplissage": round(res / cap, 3) if cap else None,
            "ca_30j_estime": round(res * 25),
            "activites": [a for a, _ in acts],
        })
    out.sort(key=lambda r: -r["resa_30j"])
    data = {"genere_le": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "fenetre": f"{d0.isoformat()} → {d1.isoformat()}",
            "deja_suivies_ecartees": deja, "candidats": out[:TOP]}
    json.dump(data, open(SORTIE, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"{SORTIE} : {len(out)} lieux actifs non suivis ({deja} déjà suivis écartés)")


if __name__ == "__main__":
    main()
