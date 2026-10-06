#!/usr/bin/env python3
"""Fusionne les doublons des stores Mindbody / Resamania (one-shot).

Contexte : seance_cle.py. Le 33 Foch et Banote identifiaient chaque séance
par un id HTML renouvelé à chaque affichage, Cercles de la Forme par un
eve_id que la source publie en plusieurs exemplaires. Les scrapers utilisent
désormais une clé métier ; ce script remet les stores existants d'aplomb.

RÈGLE DE CHOIX, par séance (jour | heure | salle | cours) :
  · s'il existe des copies figées (`finie`), on garde la PLUS ANCIENNE :
    c'est l'instantané pris au verrouillage, quelques minutes avant le
    début — celui que le scraper entendait conserver ;
  · sinon on garde la plus récente, la plus proche de l'état actuel.

RE-CLÉ : le33foch, banote et cercles_forme passent à la clé métier, celle
que leurs scrapers produiront au passage suivant — sinon une séance à venir
aurait l'ancienne entrée ET la nouvelle. DNA garde ses clés : il utilise
déjà mbo-class-id (stable) depuis août, seuls ses doublons d'avant restent.

Aucune séance réelle ne disparaît : le contrôle final vérifie que le nombre
d'entrées après fusion égale le nombre de séances distinctes avant.
"""
import collections
import json
import sys

import safestore
from seance_cle import cle_de

STORES = {
    # store                     préfixe de la nouvelle clé (None = garder)
    "le33foch_data.json":       "",
    "banote_data.json":         "",
    "cercles_forme_data.json":  "cdf|",
    "dna_data.json":            None,
}


def choisir(copies):
    figees = [c for c in copies if c[1].get("finie")]
    if figees:
        return min(figees, key=lambda c: c[1].get("releve") or "")
    return max(copies, key=lambda c: c[1].get("releve") or "")


def main():
    erreurs = 0
    for chemin, prefixe in STORES.items():
        store = safestore.load(chemin)
        groupes = collections.defaultdict(list)
        sans_date = {}
        for k, v in store.items():
            if isinstance(v, dict) and v.get("date") and v.get("heure"):
                groupes[cle_de(v)].append((k, v))
            else:
                sans_date[k] = v            # conservé tel quel
        neuf = dict(sans_date)
        for cle, copies in groupes.items():
            k, v = choisir(copies)
            if prefixe is not None:
                k = prefixe + cle
                v = {**v, "id": k}
            neuf[k] = v
        attendu = len(groupes) + len(sans_date)
        if len(neuf) != attendu:
            print(f"::error::{chemin} : {len(neuf)} entrées, {attendu} attendues")
            erreurs += 1
            continue
        safestore.save(neuf, chemin, allow_shrink=True)
        print(f"  {chemin:26} {len(store):>8,} -> {len(neuf):>7,} entrées "
              f"({len(store) - len(neuf):,} doublons fusionnés)")
    sys.exit(1 if erreurs else 0)


if __name__ == "__main__":
    main()
