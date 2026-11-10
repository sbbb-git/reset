#!/usr/bin/env python3
"""Répare les faux « réservés » créés pendant la panne Anybuddy (403, nov. 2026).

Pendant la panne, chaque passage lisait 0 créneau ; les créneaux à venir
« disparus » passaient en réservé. Un VRAI créneau réservé a disparu lors
d'un passage qui, lui, fonctionnait : son `dernier_vu` est ANTÉRIEUR au
dernier passage réussi du club. Un faux réservé était encore visible au
dernier passage réussi : son `dernier_vu` est égal à ce dernier passage.

Règle, club par club (pour le store national) ou pour tout le store
(Trinquet) : statut « reserve » ET dernier_vu ≥ dernier passage réussi
(à 2 min près) ET créneau postérieur à ce passage -> « indetermine ».
Un créneau indéterminé n'est compté ni comme réservé ni comme libre.

Idempotent. Usage : python3 repare_anybuddy_403.py
"""
import datetime as dt
import sys

import safestore

TOL = dt.timedelta(minutes=2)


def _t(s):
    try:
        return dt.datetime.fromisoformat(str(s)[:16])
    except ValueError:
        return None


def reparer(sessions, label):
    vus = [_t(v.get("dernier_vu")) for v in sessions.values() if v.get("dernier_vu")]
    vus = [x for x in vus if x]
    if not vus:
        return 0
    dernier = max(vus)
    n = 0
    for v in sessions.values():
        if v.get("statut") != "reserve":
            continue
        dv = _t(v.get("dernier_vu"))
        debut = _t(f"{v.get('date')}T{v.get('heure') or '00:00'}")
        if dv and debut and dv >= dernier - TOL and debut > dernier:
            v["statut"] = "indetermine"
            n += 1
    if n:
        print(f"  {label:55} dernier passage {dernier:%d/%m %H:%M} -> {n} faux réservés")
    return n


def main():
    total = 0
    import json
    nat = safestore.load("padel_national_data.json")
    try:
        ab = {c["slug"] for c in json.load(open("padel_national_anybuddy.json", encoding="utf-8"))}
    except (OSError, ValueError, KeyError, TypeError):
        ab = set()
    for slug, club in nat.items():
        if slug in ab:
            total += reparer(club.get("sessions") or {}, slug)
    if total:
        safestore.save(nat, "padel_national_data.json", allow_shrink=True)
    print(f"national : {total} créneaux corrigés")

    tq = safestore.load("anybuddy_data.json")
    n = reparer(tq, "Trinquet (anybuddy_data.json)")
    if n:
        safestore.save(tq, "anybuddy_data.json", allow_shrink=True)
    print(f"Trinquet : {n} créneaux corrigés")
    return 0


if __name__ == "__main__":
    sys.exit(main())
