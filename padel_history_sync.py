#!/usr/bin/env python3
"""Sync padel_idf_history.json.gz vers Supabase (table padel_slots).

One-shot : pousse les ~146k sessions historiques Doinsport (2-3 ans) en base.
Charge depuis le fichier .gz committé (87 MB brut → 6.4 MB compressé).

Idempotent : utilise resolution=merge-duplicates sur l'id du slot. Donc
ré-exécutions remettent simplement à jour les mêmes lignes.

Usage : workflow GitHub Action manuel (workflow_dispatch) avec SUPABASE_URL
+ SUPABASE_SERVICE_KEY en env vars (bypass RLS).
"""
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request

HISTORY_GZ = "padel_idf_history.json.gz"
URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
BATCH = 500


def http_post(path, body):
    if not URL or not KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY non définis")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        URL + path, data=data,
        headers={
            "apikey": KEY, "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status


def upsert(table, rows, on_conflict):
    if not rows: return 0
    suffix = f"?on_conflict={on_conflict}"
    n_ok = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i+BATCH]
        for attempt in range(3):
            try:
                http_post(f"/rest/v1/{table}{suffix}", chunk)
                n_ok += len(chunk)
                if (i // BATCH) % 20 == 0:
                    print(f"  ... {n_ok}/{len(rows)} {table}")
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "ignore")[:300]
                print(f"  ⚠️ HTTP {e.code} {table} chunk {i}: {body}", file=sys.stderr)
                if attempt == 2: raise
                time.sleep(2 ** attempt)
    return n_ok


def main():
    if not os.path.exists(HISTORY_GZ):
        print(f"❌ {HISTORY_GZ} introuvable", file=sys.stderr); sys.exit(1)

    print(f"Chargement {HISTORY_GZ}...")
    with gzip.open(HISTORY_GZ, "rt", encoding="utf-8") as f:
        store = json.load(f)

    # 1. Construire les rows padel_clubs (upsert sans écraser les unified_id existants)
    clubs_rows = []
    for slug, b in store.items():
        meta = b.get("meta") or {}
        clubs_rows.append({
            "slug": slug, "source": "doinsport",  # source canonique pour Doinsport historique
            "name": meta.get("name") or slug,
            "cp": meta.get("cp"),
            "city": meta.get("city"),
            "meta": {k: v for k, v in meta.items() if k not in {"name","cp","city","source","slug"}},
        })

    # 2. Construire les rows padel_slots (gros volume, ~146k)
    slots_rows = []
    for slug, b in store.items():
        for sid, s in (b.get("sessions") or {}).items():
            slots_rows.append({
                "id": f"{slug}|{sid}",
                "club_slug": slug,
                "date": s.get("date"),
                "heure": s.get("heure"),
                "fin": s.get("fin"),
                "duree": s.get("duree"),
                "terrain": s.get("terrain"),
                "court_id": s.get("court_id"),
                "prix": s.get("prix"),
                "statut": s.get("statut") or "reserve",
                "finie": True,             # tout est historique => terminé
                "source": "doinsport_history",
                "premier_vu": s.get("premier_vu"),
                "dernier_vu": s.get("dernier_vu"),
            })

    print(f"Sync : {len(clubs_rows)} clubs, {len(slots_rows)} slots historiques → Supabase")
    n_c = upsert("padel_clubs", clubs_rows, on_conflict="slug")
    print(f"✅ padel_clubs : {n_c} upserts")
    n_s = upsert("padel_slots", slots_rows, on_conflict="id")
    print(f"✅ padel_slots : {n_s} upserts (~{n_s/1000:.0f}k lignes)")
    print("Sync historique terminée.")


if __name__ == "__main__":
    main()
