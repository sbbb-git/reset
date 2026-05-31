#!/usr/bin/env python3
"""Sync padel_idf_data.json → Supabase (tables padel_clubs, padel_slots).

Lit le store JSON consolidé (Anybuddy + UrbanPadel + Doinsport + Playtomic)
et upsert dans Supabase via PostgREST avec la clé service_role (bypass RLS).

Usage :
  - En CI : variables d'env SUPABASE_URL + SUPABASE_SERVICE_KEY définies
  - En local : export SUPABASE_URL=... SUPABASE_SERVICE_KEY=... puis python3 ...

Stratégie idempotente :
  - padel_clubs : upsert par slug (clé primaire)
  - padel_slots : upsert par id (clé primaire) — chaque scrape met à jour le
    statut/dernier_vu, donc les disparitions détectées en réservation sont
    progressives en base.

Bonus : annote chaque club avec son unified_id (cluster cross-plateforme)
si présent dans padel_club_unified.json.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

STORE = "padel_idf_data.json"
UNIFIED = "padel_club_unified.json"
URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
BATCH = 500  # nombre de rows par requête PostgREST upsert


def http(path, body, prefer="resolution=merge-duplicates,return=minimal"):
    if not URL or not KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY non définis")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        URL + path, data=data,
        headers={
            "apikey": KEY, "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json", "Prefer": prefer,
        }, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


def upsert(table, rows, on_conflict=None):
    if not rows: return 0
    suffix = f"?on_conflict={on_conflict}" if on_conflict else ""
    n_ok = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i+BATCH]
        for attempt in range(3):
            try:
                http(f"/rest/v1/{table}{suffix}", chunk)
                n_ok += len(chunk)
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "ignore")[:300]
                print(f"  ⚠️ HTTP {e.code} upsert {table} chunk {i}: {body}", file=sys.stderr)
                if attempt == 2: raise
                time.sleep(2 ** attempt)
    return n_ok


def main():
    if not os.path.exists(STORE):
        print(f"❌ {STORE} introuvable", file=sys.stderr); sys.exit(1)
    store = json.load(open(STORE, encoding="utf-8"))

    # 1. Construire les mappings slug → unified_id
    slug_to_unified = {}
    if os.path.exists(UNIFIED):
        unified = json.load(open(UNIFIED, encoding="utf-8"))
        for cl in unified:
            for m in cl.get("members", []):
                slug_to_unified[m["slug"]] = cl["unified_id"]

    # 2. Préparer rows padel_clubs
    clubs_rows = []
    for slug, b in store.items():
        meta = b.get("meta") or {}
        source = meta.get("source") or (
            "urbanpadel" if slug.startswith("urbanpadel-") else
            "doinsport" if slug.startswith("doinsport-") else
            "playtomic" if slug.startswith("playtomic-") else "anybuddy")
        clubs_rows.append({
            "slug": slug, "source": source,
            "unified_id": slug_to_unified.get(slug),
            "name": meta.get("name") or slug,
            "cp": meta.get("cp"),
            "city": meta.get("city"),
            "lat": meta.get("lat"), "lng": meta.get("lng"),
            "meta": {k: v for k, v in meta.items() if k not in {"name","cp","city","lat","lng","source","slug"}},
        })

    # 3. Préparer rows padel_slots
    slots_rows = []
    for slug, b in store.items():
        for sid, s in (b.get("sessions") or {}).items():
            slots_rows.append({
                "id": f"{slug}|{sid}",  # globalement unique
                "club_slug": slug,
                "date": s.get("date"),
                "heure": s.get("heure"),
                "fin": s.get("fin"),
                "duree": s.get("duree"),
                "terrain": s.get("terrain"),
                "court_id": s.get("court_id"),
                "prix": s.get("prix"),
                "statut": s.get("statut"),
                "finie": bool(s.get("finie")),
                "source": s.get("source") or (
                    "urbanpadel" if slug.startswith("urbanpadel-") else
                    "doinsport" if slug.startswith("doinsport-") else
                    "playtomic" if slug.startswith("playtomic-") else "anybuddy"),
                "premier_vu": s.get("premier_vu"),
                "dernier_vu": s.get("dernier_vu"),
            })

    print(f"À syncer : {len(clubs_rows)} clubs, {len(slots_rows)} slots → Supabase")
    n_clubs = upsert("padel_clubs", clubs_rows, on_conflict="slug")
    print(f"  ✅ padel_clubs : {n_clubs} upserts")
    n_slots = upsert("padel_slots", slots_rows, on_conflict="id")
    print(f"  ✅ padel_slots : {n_slots} upserts")
    print("Sync OK.")


if __name__ == "__main__":
    main()
