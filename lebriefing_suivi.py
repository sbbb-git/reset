#!/usr/bin/env python3
"""Suivi de la demande de Le Briefing (lebriefing.co), hors scrap principal.

Le Briefing ne publie ni fréquentation ni chiffre d'affaires, et Eventbrite
n'affiche pas les billets vendus : seulement « en vente » ou « complet ».
On reconstruit donc l'historique en relevant, à chaque passage, l'état de
chaque événement. Ce que ça donne avec le temps :
  · la date d'ouverture des ventes et, le cas échéant, celle du passage
    en complet -> délai de remplissage (meilleur indicateur de demande) ;
  · la part d'événements complets, par ville et par lieu ;
  · le prix et ses évolutions.

Sources : la page d'accueil de lebriefing.co (liste des événements, toutes
villes) et la page publique Eventbrite de chaque événement. Un événement
reste suivi jusqu'à sa date, même retiré de l'accueil.

Si un site refuse l'accès (401/403/429), le passage s'arrête sans rien
modifier : on ne contourne pas un refus.

Sorties : lebriefing_suivi.json (état + historique) et lebriefing_suivi.csv.
"""
import csv
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
ACCUEIL = "https://www.lebriefing.co/"
ETAT = "lebriefing_suivi.json"
CSV_PATH = "lebriefing_suivi.csv"
UA = "Mozilla/5.0"
PAUSE = 1.5            # secondes entre deux pages Eventbrite


class Refus(RuntimeError):
    pass


def lire(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            raise Refus(f"HTTP {e.code} sur {url}") from e
        raise


def ident(url):
    m = re.search(r"(\d{9,})(?:\D*$)", url.split("?")[0])
    return m.group(1) if m else url


def g(pat, t):
    m = re.search(pat, t)
    return html.unescape(m.group(1)) if m else None


def analyser(page):
    statut = g(r'"salesStatus":\{"salesStatus":"([a-z_]+)"', page)
    return {
        "titre": g(r'"name":"([^"]{5,200})","description"', page) or g(r"<title>([^<|]+)", page),
        "debut": g(r'"startDate":"(\d{4}-\d\d-\d\dT[^"]+)"', page),
        "lieu": g(r'"location":\{"@type":"Place","name":"([^"]+)"', page),
        "adresse": g(r'"streetAddress":"([^"]+)"', page),
        "ville": g(r'"addressLocality":"([^"]+)"', page),
        "prix_min": g(r'"lowPrice":"([^"]+)"', page),
        "prix_max": g(r'"highPrice":"([^"]+)"', page),
        "statut": statut,                                  # on_sale, sold_out, sales_ended…
        "disponibilite": g(r'"availability":"([^"]+)"', page),
        "ventes_ouvertes": g(r'"startSalesDate":\{[^}]*"local":"([^"]+)"', page),
    }


def charger():
    if os.path.exists(ETAT):
        with open(ETAT, encoding="utf-8") as f:
            return json.load(f)
    return {"releves": 0, "events": {}}


def main():
    now = dt.datetime.now(PARIS)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    etat = charger()
    ev = etat["events"]
    try:
        accueil = lire(ACCUEIL)
    except Refus as e:
        print(f"::warning::{e} — passage annulé, rien de modifié")
        return 0
    urls = sorted(set(re.findall(r"https://www\.eventbrite\.(?:fr|com)/e/[^\"'?#\s<]+", accueil)))
    for u in urls:
        ev.setdefault(ident(u), {"url": u, "premier_vu": stamp, "historique": []})
    # suivis : présents sur l'accueil, ou pas encore passés
    a_lire = [k for k, e in ev.items()
              if e.get("statut") != "passé" and
              (not e.get("debut") or e["debut"][:10] >= now.date().isoformat())]
    lus = 0
    for k in a_lire:
        e = ev[k]
        try:
            info = analyser(lire(e["url"]))
        except Refus as err:
            print(f"::warning::{err} — arrêt du passage (déjà lus : {lus})")
            break
        except Exception as err:  # noqa: BLE001
            print(f"  ⚠️ {e['url']} : {type(err).__name__} {str(err)[:80]}", file=sys.stderr)
            continue
        lus += 1
        ancien = e.get("statut")
        e.update({c: v for c, v in info.items() if v is not None})
        e["dernier_releve"] = stamp
        if info.get("statut") and info["statut"] != ancien:
            e["historique"].append({"t": stamp, "statut": info["statut"],
                                    "prix_max": info.get("prix_max")})
            if info["statut"] == "sold_out":
                # complet dès le premier relevé : date de complet inconnue
                e["complet_le"] = None if ancien is None else stamp
                e["complet_des_1er_releve"] = ancien is None
        time.sleep(PAUSE)
    # événements terminés : statut figé
    # Après la date, Eventbrite affiche « sales_ended » même pour une soirée
    # complète : le statut final retient « sold_out » s'il a été vu une fois.
    for e in ev.values():
        if any(h.get("statut") == "sold_out" for h in e.get("historique", [])):
            e["a_ete_complet"] = True
        if e.get("debut") and e["debut"][:10] < now.date().isoformat() and e.get("statut") != "passé":
            e["statut_final"] = "sold_out" if e.get("a_ete_complet") else e.get("statut")
            e["statut"] = "passé"
    etat["releves"] = etat.get("releves", 0) + 1
    etat["dernier_releve"] = stamp
    tmp = ETAT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(etat, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ETAT)
    ecrire_csv(ev)
    n_complets = sum(1 for e in ev.values() if e.get("a_ete_complet"))
    print(f"Le Briefing {stamp} : {len(urls)} sur l'accueil, {lus} pages lues, "
          f"{len(ev)} événements suivis, {n_complets} complets")
    return 0


def jours(a, b):
    try:
        return round((dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)).total_seconds() / 86400, 1)
    except (TypeError, ValueError):
        return ""


def ecrire_csv(ev):
    cols = ["event_id", "date", "heure", "ville", "lieu", "titre", "prix_min", "prix_max",
            "statut", "statut_final", "ventes_ouvertes", "premier_vu", "complet_le",
            "jours_pour_complet", "complet_des_1er_releve", "url"]
    rows = []
    for k, e in ev.items():
        d = e.get("debut") or ""
        rows.append({
            "event_id": k, "date": d[:10], "heure": d[11:16], "ville": e.get("ville"),
            "lieu": e.get("lieu"), "titre": e.get("titre"), "prix_min": e.get("prix_min"),
            "prix_max": e.get("prix_max"), "statut": e.get("statut"),
            "statut_final": e.get("statut_final", ""), "ventes_ouvertes": (e.get("ventes_ouvertes") or "")[:16],
            "premier_vu": e.get("premier_vu"), "complet_le": e.get("complet_le") or "",
            "jours_pour_complet": jours(e.get("ventes_ouvertes"), e.get("complet_le")) if e.get("complet_le") else "",
            "complet_des_1er_releve": "oui" if e.get("complet_des_1er_releve") else "",
            "url": e.get("url"),
        })
    rows.sort(key=lambda r: (r["date"], r["heure"]))
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    sys.exit(main())
