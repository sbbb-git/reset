#!/usr/bin/env python3
"""Veille des nouveaux lieux : recense TOUT bsport, signale ce qu'on ne suit pas.

POURQUOI ÇA MARCHE
L'API bsport expose /book/v1/establishment/?company=<N> sans authentification,
et les identifiants de company sont de petits entiers contigus. On peut donc
balayer l'espace complet et obtenir un recensement exhaustif : titre, adresse
structurée (zipcode, city, state), coordonnées, et `has_next_slots` qui dit si
le lieu a déjà des créneaux réservables.

C'est la même stratégie que padel_national_discover.py, qui énumère les
catalogues Doinsport / Playtomic / UrbanPadel : on ne devine pas les lieux, on
lit la liste que la plateforme publie déjà.

CE QUE ÇA DÉTECTE
Un studio qui ouvre à Paris et prend bsport apparaît dans le balayage suivant.
En comparant avec nos catalogues, on isole ce qu'on ne scrape pas encore.

CE QUE ÇA NE DÉTECTE PAS, ET IL FAUT LE SAVOIR
Uniquement les lieux sur bsport. Un studio qui ouvre sur Mindbody, Deciplus ou
son propre site reste invisible ici. bsport couvre 91 de nos marques, c'est le
plus gros gisement, mais ce n'est pas tout le marché — ne pas lire ce rapport
comme « les nouvelles ouvertures de Paris ».

Le balayage est repris là où il s'est arrêté (état persistant), avec un budget
temps, pour tenir dans un workflow sans le faire exploser.
"""
import datetime as dt
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

API = "https://api.production.bsport.io"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"

ETAT = "veille_lieux_etat.json"        # curseur + lieux déjà vus
RAPPORT = "veille_nouveaux_lieux.json"  # ce que la page HTML consomme

COMPANY_MAX = 8000        # au-delà, l'espace est vide aujourd'hui
LOT = 400                 # companies sondées par passage
WORKERS = 8
MAX_SECONDS = 12 * 60
MARGE_FRONTIERE = 500   # ids sondés au-dessus de la frontière connue

DEPTS_IDF = ("75", "77", "78", "91", "92", "93", "94", "95")


def _get(path, params, retries=3):
    url = f"{API}{path}?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404):
                return None
            time.sleep(1.5 * (attempt + 1))
        except Exception:  # noqa: BLE001
            time.sleep(1.5 * (attempt + 1))
    return None


def etablissements(company):
    d = _get("/book/v1/establishment/", {"company": company, "page_size": 50})
    if isinstance(d, dict):
        return d.get("results") or []
    return d if isinstance(d, list) else []


def est_idf(loc):
    """Île-de-France : on croise l'état et le code postal, l'un des deux
    pouvant manquer selon la fiche."""
    if (loc.get("country") or "").strip().lower() not in ("", "france"):
        return False
    if (loc.get("state") or "").strip().lower() in ("île-de-france", "ile-de-france"):
        return True
    return str(loc.get("zipcode") or "").strip()[:2] in DEPTS_IDF


def charger_connus():
    """Tout ce qu'on suit déjà : company_id bsport et noms normalisés.

    Trois sources, parce qu'aucune n'est complète à elle seule :
      · *_extension_resolved.json — mais 46 de ses 95 entrées bsport n'ont
        pas encore de company_id (les cas « ID introuvable », que le suivi
        des bundles JS dans detect_bsport résout au fil des passages) ;
      · discovery.json — le catalogue historique, ids sous ids.company ;
      · *_extension_brands.json — pour rapprocher par nom ce qu'on ne peut
        pas rapprocher par id.
    Sans ces trois-là, la veille signalerait comme neuf ce qu'on scrape déjà.
    """
    companies, noms = set(), set()

    for f in glob.glob("*_extension_resolved.json"):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for k, v in d.items():
            if k.startswith("_") or not isinstance(v, dict):
                continue
            if v.get("platform") == "bsport" and v.get("company_id"):
                companies.add(str(v["company_id"]))

    try:
        for e in json.load(open("discovery.json", encoding="utf-8")):
            if not isinstance(e, dict):
                continue
            cid = ((e.get("ids") or {}).get("company")
                   if isinstance(e.get("ids"), dict) else None)
            if cid:
                companies.add(str(cid))
            if e.get("name"):
                noms.add(_norm(e["name"]))
    except (OSError, ValueError):
        pass

    for f in glob.glob("*_extension_brands.json"):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for k, v in d.items():
            if k.startswith("_") or not isinstance(v, dict):
                continue
            noms.add(_norm(v.get("label") or k))

    return companies, noms


def _norm(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


_GENERIQUE = re.compile(
    r"^(salle|studio|espace|cours|room)\s*\d*\s*$|accueil|domicile|"
    r"^(parc|jardin|gymnase|stade|square|march[ée])\b", re.I)


def _enseigne(lieux):
    """Nom d'enseigne déduit des titres d'établissement.

    bsport n'expose pas le nom de l'entreprise (`related_company` ne rend que
    son id, et /book/v1/company/<id>/ répond 404). On prend donc le titre le
    plus parlant : le premier qui ne ressemble pas à un nom de salle ou de
    lieu public, à défaut le plus long.
    """
    noms = [l.get("nom") or "" for l in lieux if l.get("nom")]
    if not noms:
        return "(sans nom)"
    parlants = [n for n in noms if not _GENERIQUE.search(n)]
    pool = parlants or noms
    return max(pool, key=len)


def charger_etat():
    if os.path.exists(ETAT):
        try:
            return json.load(open(ETAT, encoding="utf-8"))
        except ValueError:
            pass
    return {"curseur": 1, "lieux": {}, "tours": 0}


def sauver(obj, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _balayer(plage, lieux, connus_co, connus_noms, aujourdhui, deadline):
    """Sonde une plage de companies. Renvoie (nouvelles fiches, vus, arret).

    `arret` vaut l'id où le budget temps a coupé, sinon None.
    """
    nouveaux, vus, arret, frontiere = [], 0, None, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(etablissements, c): c for c in plage}
        for f in as_completed(futs):
            cid = futs[f]
            if time.time() > deadline:
                arret = cid if arret is None else min(arret, cid)
                continue
            try:
                ets = f.result()
            except Exception:  # noqa: BLE001
                continue
            if ets:
                frontiere = max(frontiere, cid)
            for e in ets or []:
                loc = e.get("location") or {}
                if not est_idf(loc):
                    continue
                vus += 1
                cle = f"bsport-{cid}-{e.get('id')}"
                if cle in lieux:
                    lieux[cle]["dernier_vu"] = aujourdhui
                    lieux[cle]["creneaux"] = bool(e.get("has_next_slots"))
                    continue
                fiche = {
                    "cle": cle, "company_id": str(cid), "establishment_id": e.get("id"),
                    "nom": e.get("title") or "", "adresse": loc.get("address") or "",
                    "cp": loc.get("zipcode") or "", "ville": loc.get("city") or "",
                    "lat": loc.get("latitude"), "lng": loc.get("longitude"),
                    "creneaux": bool(e.get("has_next_slots")),
                    "desactive": bool(e.get("disabled")),
                    "premier_vu": aujourdhui, "dernier_vu": aujourdhui,
                    "deja_suivi": (str(cid) in connus_co
                                   or _norm(e.get("title")) in connus_noms),
                }
                lieux[cle] = fiche
                if not fiche["deja_suivi"]:
                    nouveaux.append(fiche)
    return nouveaux, vus, arret, frontiere


def main():
    deadline = time.time() + MAX_SECONDS
    etat = charger_etat()
    connus_co, connus_noms = charger_connus()
    lieux = etat["lieux"]
    aujourdhui = dt.date.today().isoformat()

    # ---- 1. FRONTIÈRE : c'est ça, la veille ----------------------------
    # Une affaire qui vient d'ouvrir reçoit un company_id neuf, donc en haut
    # de l'espace. Balayer depuis 1 ne trouverait que de l'historique : on
    # regarde d'abord au-dessus du plus haut id peuplé connu.
    front = int(etat.get("frontiere") or 6600)
    plage_front = range(max(1, front - 100), front + MARGE_FRONTIERE)
    print(f"[VEILLE] frontière : companies {plage_front.start} → {plage_front.stop - 1}")
    neufs, vus_f, _, front_vu = _balayer(plage_front, lieux, connus_co,
                                         connus_noms, aujourdhui, deadline)
    if front_vu:
        etat["frontiere"] = max(front, front_vu)

    # ---- 2. RATTRAPAGE : le reste de l'espace, par lots ----------------
    depart = int(etat.get("curseur") or 1)
    vus_r = 0
    rattrapage = []
    if time.time() < deadline:
        if depart > COMPANY_MAX:
            depart = 1
            etat["tours"] = int(etat.get("tours") or 0) + 1
            print(f"[VEILLE] espace bouclé, tour {etat['tours']} — reprise à 1")
        arrivee = min(depart + LOT, COMPANY_MAX + 1)
        print(f"[VEILLE] rattrapage : companies {depart} → {arrivee - 1}")
        rattrapage, vus_r, arret, _ = _balayer(range(depart, arrivee), lieux,
                                               connus_co, connus_noms,
                                               aujourdhui, deadline)
        etat["curseur"] = arret if arret else arrivee

    nouveaux = neufs + rattrapage
    etat["lieux"] = lieux
    etat["dernier_passage"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    sauver(etat, ETAT)

    # Rapport groupé par ENSEIGNE, pas par salle. Une company bsport peut
    # exposer « Salle 1 », « Salle 2 », « Parc Monceau », « Cours à domicile » :
    # ce sont des espaces d'une même affaire, pas des ouvertures distinctes.
    # Les lister à plat noyait le signal sous les salles et les lieux loués.
    par_enseigne = {}
    for v in lieux.values():
        if v.get("deja_suivi") or v.get("desactive"):
            continue
        g = par_enseigne.setdefault(v["company_id"], {
            "company_id": v["company_id"], "lieux": [],
            "creneaux": False, "premier_vu": v.get("premier_vu"),
        })
        g["lieux"].append({k: v[k] for k in
                           ("nom", "adresse", "cp", "ville", "lat", "lng", "creneaux")})
        g["creneaux"] = g["creneaux"] or v.get("creneaux", False)
        if (v.get("premier_vu") or "") < (g["premier_vu"] or "9"):
            g["premier_vu"] = v.get("premier_vu")

    for g in par_enseigne.values():
        g["nb_lieux"] = len(g["lieux"])
        g["enseigne"] = _enseigne(g["lieux"])
        g["cp"] = next((l["cp"] for l in g["lieux"] if l.get("cp")), "")
        g["ville"] = next((l["ville"] for l in g["lieux"] if l.get("ville")), "")

    # Tri : créneaux ouverts d'abord (l'affaire tourne, elle est scrapable
    # tout de suite), puis company_id décroissant. L'id est le meilleur
    # proxy d'ancienneté dont on dispose — bsport les attribue en séquence,
    # donc un id haut = une inscription récente = une ouverture probable.
    a_traiter = sorted(par_enseigne.values(),
                       key=lambda g: (g["creneaux"], int(g["company_id"])),
                       reverse=True)
    sauver({"genere": etat["dernier_passage"],
            "couverture": f"companies bsport 1 → {etat['curseur'] - 1} "
                          f"(sur {COMPANY_MAX}), tour {etat.get('tours', 0)}",
            "total_idf_recenses": len(lieux),
            "enseignes_non_suivies": len(a_traiter),
            "avec_creneaux": sum(1 for g in a_traiter if g["creneaux"]),
            "enseignes": a_traiter}, RAPPORT)
    ecrire_page(json.load(open(RAPPORT, encoding="utf-8")))

    avec = [g for g in a_traiter if g["creneaux"]]
    print(f"[VEILLE] {vus_f + vus_r} établissements IDF vus, {len(nouveaux)} salles inconnues "
          f"ce passage — {len(a_traiter)} enseignes non suivies, dont "
          f"{len(avec)} avec créneaux ouverts")
    for g in avec[:25]:
        pl = f" ({g['nb_lieux']} lieux)" if g["nb_lieux"] > 1 else ""
        print(f"   • {g['enseigne'][:38]:<38} {g['cp']:<6} {g['ville'][:18]:<18} "
              f"company={g['company_id']:<5}{pl}")
    if len(avec) > 25:
        print(f"   … et {len(avec) - 25} autres avec créneaux, voir {RAPPORT}")


def ecrire_page(rapport):
    """Page HTML du hub. Volontairement sobre : c'est une liste de pistes à
    trier à la main, pas un dashboard de mesure."""
    import html as _h
    try:
        from template_common import CSS_COMMON
    except ImportError:
        CSS_COMMON = "<style>body{font-family:system-ui;margin:24px}</style>"

    lignes = []
    for g in rapport["enseignes"]:
        adr = next((l["adresse"] for l in g["lieux"] if l.get("adresse")), "")
        maps = ("https://www.google.com/maps/search/?api=1&query="
                + urllib.parse.quote(adr or g["enseigne"]))
        badge = ('<span class="ok">créneaux ouverts</span>' if g["creneaux"]
                 else '<span class="attente">pas encore de créneaux</span>')
        pl = (f'<span class="muted"> · {g["nb_lieux"]} lieux</span>'
              if g["nb_lieux"] > 1 else "")
        lignes.append(
            f'<tr><td><b>{_h.escape(g["enseigne"])}</b>{pl}</td>'
            f'<td>{_h.escape(g.get("cp") or "")} {_h.escape(g.get("ville") or "")}</td>'
            f'<td><a href="{maps}" target="_blank" rel="noopener">'
            f'{_h.escape(adr[:60])}</a></td>'
            f'<td>{badge}</td><td class="muted">{g["company_id"]}</td></tr>')

    html = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veille — nouveaux lieux IDF</title>{CSS_COMMON}
<style>
 table{{width:100%;border-collapse:collapse;margin-top:16px}}
 th,td{{padding:9px 10px;border-bottom:1px solid var(--line,#2a2a2a);
        text-align:left;font-size:14px;vertical-align:top}}
 th{{font-size:12px;text-transform:uppercase;letter-spacing:.08em;opacity:.65}}
 .ok{{color:#2ecc71;font-weight:600}} .attente{{opacity:.5}}
 .muted{{opacity:.5;font-weight:400}}
 .avert{{background:rgba(255,180,0,.09);border:1px solid rgba(255,180,0,.35);
         border-radius:10px;padding:12px 16px;margin:16px 0;font-size:14px;line-height:1.55}}
</style></head><body>
<h1>Veille — nouveaux lieux en Île-de-France</h1>
<p class="muted">Recensement automatique de bsport · généré le {rapport['genere']}<br>
{rapport['total_idf_recenses']} établissements IDF recensés ·
<b>{rapport['enseignes_non_suivies']} enseignes non suivies</b>, dont
<b>{rapport['avec_creneaux']} avec des créneaux ouverts</b><br>
Couverture : {rapport['couverture']}</p>

<div class="avert"><b>À lire avant d'exploiter cette liste.</b>
Elle ne couvre que <b>bsport</b>. Un lieu qui ouvre sur Mindbody, Deciplus ou
son propre site n'y apparaîtra pas — bsport est notre plus gros gisement
(91 marques), pas tout le marché.
Le classement met en tête les identifiants les plus hauts, parce que bsport les
attribue en séquence : un id élevé signale une inscription récente, donc une
ouverture probable. C'est un indice, pas une date d'ouverture.
Enfin bsport héberge aussi des théâtres, des associations et des cours en
ligne : la liste est une base de pistes à trier, pas un résultat.</div>

<table><thead><tr><th>Enseigne</th><th>Commune</th><th>Adresse</th>
<th>Activité</th><th>company</th></tr></thead>
<tbody>{''.join(lignes)}</tbody></table>
</body></html>"""
    # CSS_COMMON est paramétré par les scrapers via .replace() ; ici il n'y a
    # pas de marque, donc on résout les accents nous-mêmes.
    html = html.replace("__ACCENT2__", "#d4b8ff").replace("__ACCENT__", "#b07ff0")
    with open("veille.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"-> veille.html ({len(rapport['enseignes'])} enseignes)")


if __name__ == "__main__":
    main()
