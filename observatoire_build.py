#!/usr/bin/env python3
"""Construit observatoire_data.json, le jeu consolidé de l'Observatoire.

Lit tous les stores de séances (<store>_data.json), les rattache à une
marque, un groupe et un site géolocalisé (observatoire_registry), et produit
des agrégats compacts que observatoire.html filtre côté navigateur :

  marques  : métadonnées (groupe, secteur, fiabilité, prix moyen…)
  sites    : un emplacement physique par marque (ville, cp, région)
  jours    : par site et par jour — séances, séances à capacité connue,
             capacité, présents, séances complètes
  heat     : par site — jour de semaine × heure (séances, présents, capacité)
  cours    : par marque — les 40 cours les plus programmés
  coachs   : par marque — les 40 coachs les plus programmés

FIABILITÉ
  exact   → plateformes qui publient capacité et réservations (bsport,
            Mariana Tek, Mindbody via proxy…) : remplissage = présents / capacité.
  statut  → widgets Mindbody & co qui n'affichent qu'un statut (complet,
            presque complet, disponible) : présents = plancher estimé par le
            scraper, remplissage non comparable — seul le % complet l'est.

Seules les séances terminées (`finie`) et datées d'aujourd'hui ou avant sont
comptées. Usage : python3 observatoire_build.py
"""
import collections
import datetime as dt
import json
import os
import sys

import observatoire_registry as reg

SORTIE = "observatoire_data.json"
MIN_SEANCES_LIEU = 60        # en dessous, un lieu sans repère rejoint le site principal


def charger_store(store):
    try:
        with open(f"{store}_data.json", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    vals = d.values() if isinstance(d, dict) else d
    return [v for v in vals if isinstance(v, dict) and v.get("date")]


def num(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def main():
    today = dt.date.today().isoformat()
    marques = reg.marques()
    out_marques, out_sites = [], []
    site_idx = {}
    jours = collections.defaultdict(lambda: [0, 0, 0, 0.0, 0.0, 0])   # (site, date)
    heat = collections.defaultdict(lambda: [0, 0.0, 0.0])            # (site, wd, h)
    cours = collections.defaultdict(lambda: [0, 0.0, 0.0, 0])        # (marque, cours)
    coachs = collections.defaultdict(lambda: [0, 0.0, 0.0, 0])
    dmin = "9999"

    for key, m in marques.items():
        # Plusieurs stores pour une marque (Club Pilates…) : une séance déjà
        # vue dans un store précédent est ignorée. Pas de dédoublonnage à
        # l'intérieur d'un store — deux créneaux identiques y sont réels
        # (bains froids en parallèle, cours dédoublés).
        rows, vus = [], set()
        for store in m["stores"]:
            ids = set()
            for r in charger_store(store):
                ident = (r.get("date"), r.get("heure"), r.get("lieu"), r.get("cours"))
                if ident in vus:
                    continue
                ids.add(ident)
                rows.append(r)
            vus |= ids
        rows = [r for r in rows if r.get("finie") and str(r["date"])[:10] <= today]
        if not rows:
            continue
        statut = sum(1 for r in rows if "statut" in r) > len(rows) / 2
        fiab = "statut" if statut else "exact"

        # lieu brut -> site
        par_lieu = collections.Counter(r.get("lieu") for r in rows)
        brut = {}
        for lieu in par_lieu:
            brut[lieu] = reg.site_du_lieu(m, lieu)
        poids = collections.Counter()
        for lieu, (nom, cp, typ) in brut.items():
            if nom:
                poids[(nom, cp, typ)] += par_lieu[lieu]
        if not poids:
            continue
        principal = max((k for k in poids if k[2] == "studio"), key=lambda k: poids[k],
                        default=next(iter(poids)))
        # Marque multi-sites : un lieu sans repère (« Pilates reformer ») ne
        # peut pas être rattaché au siège sans fausser la carte -> site « Non
        # localisé », sauf s'il est marginal (événement, test) -> site principal.
        localise = any(reg.geo_du_lieu(l) for l in par_lieu)
        for lieu, (nom, cp, typ) in brut.items():
            if nom and typ == "studio" and reg.geo_du_lieu(lieu) is None:
                if par_lieu[lieu] < MIN_SEANCES_LIEU:
                    brut[lieu] = principal
                elif localise:
                    brut[lieu] = ("Non localisé", "", "non localisé")

        n_marque = 0
        for r in rows:
            nom, cp, typ = brut.get(r.get("lieu"), (None, None, None))
            if not nom:
                continue
            sk = (key, nom)
            if sk not in site_idx:
                if typ == "studio":
                    ville, quartier = reg.ville_de(cp)
                    region = reg.region_de(cp)
                else:
                    ville = quartier = region = typ.capitalize()
                site_idx[sk] = len(out_sites)
                out_sites.append({"id": len(out_sites), "marque": key, "nom": nom,
                                  "cp": cp or "", "ville": ville, "quartier": quartier,
                                  "region": region})
            sid = site_idx[sk]
            d = str(r["date"])[:10]
            dmin = min(dmin, d)
            cap, pres = num(r.get("capacite")), num(r.get("presents"))
            st = str(r.get("statut") or "").lower()
            complet = (st == "complet") if statut else (cap > 0 and pres >= cap)
            j = jours[(sid, d)]
            j[0] += 1
            if cap > 0 and not statut:
                j[1] += 1
                j[2] += cap
                j[3] += min(pres, cap)
            j[4] += pres
            j[5] += 1 if complet else 0
            try:
                wd = dt.date.fromisoformat(d).weekday()
                h = int(str(r.get("heure") or "0")[:2])
            except ValueError:
                wd, h = None, None
            if wd is not None:
                hh = heat[(sid, wd, h)]
                hh[0] += 1
                hh[1] += pres
                hh[2] += cap if not statut else 0
            c = (r.get("cours") or "").strip()[:60]
            if c:
                cc = cours[(key, c)]
                cc[0] += 1; cc[1] += pres; cc[2] += cap if not statut else 0; cc[3] += complet
            co = (r.get("coach") or "").strip()[:40]
            if co:
                k = coachs[(key, co)]
                k[0] += 1; k[1] += pres; k[2] += cap if not statut else 0; k[3] += complet
            n_marque += 1
        if not n_marque:
            continue
        out_marques.append({k: m[k] for k in ("key", "label", "groupe", "secteur",
                                                "categorie", "prix")}
                           | {"fiabilite": fiab,
                              "page": m["page"] if os.path.exists(m["page"]) else None})

    base = dt.date.fromisoformat(dmin)
    par_site = collections.defaultdict(list)
    for (sid, d), v in sorted(jours.items()):
        par_site[sid].append([(dt.date.fromisoformat(d) - base).days, v[0], v[1],
                              round(v[2]), round(v[3]), round(v[4]), v[5]])
    heat_out = collections.defaultdict(list)
    for (sid, wd, h), v in sorted(heat.items()):
        heat_out[sid].append([wd, h, v[0], round(v[1]), round(v[2])])

    def top(src, n=40):
        par = collections.defaultdict(list)
        for (mk, nom), v in src.items():
            par[mk].append([nom, v[0], round(v[1]), round(v[2]), v[3]])
        return {mk: sorted(l, key=lambda x: -x[1])[:n] for mk, l in par.items()}

    data = {
        "genere_le": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "base": base.isoformat(),
        "colonnes_jours": ["jour", "seances", "seances_cap", "capacite", "presents_cap",
                           "presents", "complets"],
        "marques": sorted(out_marques, key=lambda b: b["label"].lower()),
        "sites": out_sites,
        "jours": {str(k): v for k, v in par_site.items()},
        "heat": {str(k): v for k, v in heat_out.items()},
        "cours": top(cours),
        "coachs": top(coachs),
    }
    tmp = SORTIE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, SORTIE)
    n_j = sum(len(v) for v in par_site.values())
    print(f"{SORTIE} : {len(out_marques)} marques, {len(out_sites)} sites, "
          f"{n_j:,} jours-site, {os.path.getsize(SORTIE) / 1e6:.2f} Mo")


if __name__ == "__main__":
    sys.exit(main())
