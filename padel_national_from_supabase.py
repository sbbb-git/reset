#!/usr/bin/env python3
"""Reconstruit padel_national_data.json depuis Supabase.

Backward-compat : tant que des HTML / scripts lisent encore le fichier
JSON local, le workflow CI :
  1. scrape → padel_national_data.json (énorme, ~98 MB)
  2. push to Supabase via padel_national_supabase_sync.py
  3. reconstruct local padel_national_data.json depuis Supabase
     (qui sera ensuite élagué par prune_padel_live.py à ~5 MB)

Le rebuild ne récupère QUE les clubs marqués metro != 'idf' afin de ne
pas écraser le périmètre IDF (qui a son propre store padel_idf_data.json).

Pagination PostgREST par range (chunks de 1000 lignes).

Usage :
  - En CI : SUPABASE_URL + SUPABASE_SERVICE_KEY (ou SUPABASE_ANON_KEY) définis
  - En local : export SUPABASE_URL=... SUPABASE_SERVICE_KEY=... puis python3 ...
"""
import json
import os
import sys
import urllib.parse
import urllib.request

STORE = "padel_national_data.json"
URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
PAGE = 1000


def http_get(path):
    if not URL or not KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY non définis")
    req = urllib.request.Request(
        URL + path,
        headers={
            "apikey": KEY, "Authorization": f"Bearer {KEY}",
            "Accept": "application/json",
        }, method="GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_all(table, select="*", extra=""):
    """Paginé via offset/limit query params."""
    rows = []
    offset = 0
    while True:
        q = f"select={urllib.parse.quote(select)}&limit={PAGE}&offset={offset}"
        if extra:
            q += "&" + extra
        chunk = http_get(f"/rest/v1/{table}?{q}")
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        offset += PAGE
    return rows


def main():
    # 1. Clubs hors IDF (metro != 'idf' OU metro NULL — on prend tout sauf idf)
    print("Fetch padel_clubs (metro != idf)...")
    clubs = fetch_all("padel_clubs", select="*", extra="metro=neq.idf")
    print(f"  → {len(clubs)} clubs")

    slugs = {c["slug"] for c in clubs}
    if not slugs:
        print("Aucun club national en base. Abandon (pas de reconstruction).")
        return

    # 2. Slots de ces clubs (filtre côté serveur en IN, par chunks pour pas
    #    dépasser la longueur d'URL).
    print("Fetch padel_slots des clubs nationaux...")
    slots = []
    slug_list = list(slugs)
    CHUNK = 200
    for i in range(0, len(slug_list), CHUNK):
        sub = slug_list[i:i+CHUNK]
        in_clause = "(" + ",".join(urllib.parse.quote(s) for s in sub) + ")"
        chunk_slots = fetch_all(
            "padel_slots", select="*",
            extra=f"club_slug=in.{in_clause}")
        slots.extend(chunk_slots)
        print(f"  chunk {i//CHUNK+1}: +{len(chunk_slots)} (total {len(slots)})")

    print(f"  → {len(slots)} slots")

    # 3. Reconstruire la structure du store :
    #    { slug: { meta: {...}, sessions: { session_id: {...} } } }
    store = {}
    for c in clubs:
        meta_extra = c.get("meta") or {}
        meta = {
            "slug": c["slug"],
            "name": c.get("name"),
            "cp": c.get("cp"),
            "city": c.get("city"),
            "lat": c.get("lat"),
            "lng": c.get("lng"),
            "source": c.get("source"),
            "metro": c.get("metro"),
            **meta_extra,
        }
        # nettoyer les None pour limiter le bruit
        meta = {k: v for k, v in meta.items() if v is not None}
        store[c["slug"]] = {"meta": meta, "sessions": {}}

    for s in slots:
        slug = s.get("club_slug")
        if slug not in store:
            continue
        # id en base = "{slug}|{sid}" → extraire sid
        full_id = s.get("id") or ""
        sid = full_id.split("|", 1)[1] if "|" in full_id else full_id
        session = {
            "id": sid,
            "date": s.get("date"),
            "heure": s.get("heure"),
            "fin": s.get("fin"),
            "duree": s.get("duree"),
            "terrain": s.get("terrain"),
            "court_id": s.get("court_id"),
            "prix": s.get("prix"),
            "statut": s.get("statut"),
            "finie": bool(s.get("finie")),
            "source": s.get("source"),
            "premier_vu": s.get("premier_vu"),
            "dernier_vu": s.get("dernier_vu"),
        }
        session = {k: v for k, v in session.items() if v is not None}
        store[slug]["sessions"][sid] = session

    # 4. Écriture compacte (même format que padel_idf_data.json)
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(STORE)
    n_sessions = sum(len(b["sessions"]) for b in store.values())
    print(f"✅ {STORE} reconstruit : {len(store)} clubs, {n_sessions} sessions, {size/1e6:.1f} MB")


if __name__ == "__main__":
    main()
