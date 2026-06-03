#!/usr/bin/env python3
"""Élague padel_idf_data.json et padel_national_data.json.

Objectif : limiter la taille du repo (32 MB + 98 MB → ~5 MB chacun) en
gardant seulement les sessions récentes/futures + 1 fenêtre passée courte.

Politique :
- on garde toutes les sessions dont la date >= aujourd'hui - WINDOW_DAYS
- on garde toutes les sessions futures (jusqu'à WINDOW_FUTURE_DAYS)
- les sessions plus anciennes que WINDOW_DAYS sont supposées déjà
  synchronisées dans Supabase (table padel_slots) et dans le .gz d'archive
  via padel_history_sync.py

Idempotent : à exécuter dans le workflow padel après les scrapers + le sync
Supabase, AVANT le commit.
"""
import datetime as dt
import json
import os
import sys

WINDOW_DAYS = 30          # garde 30 jours d'historique vivant
WINDOW_FUTURE_DAYS = 60   # garde 60 jours de futur (booking window large)
STORES = ["padel_idf_data.json", "padel_national_data.json"]


def prune_one(path):
    if not os.path.exists(path):
        print(f"  skip {path} (absent)")
        return
    size_before = os.path.getsize(path)
    with open(path, encoding="utf-8") as f:
        store = json.load(f)
    if not isinstance(store, dict):
        print(f"  skip {path} (format inattendu)")
        return

    today = dt.date.today()
    min_date = (today - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    max_date = (today + dt.timedelta(days=WINDOW_FUTURE_DAYS)).isoformat()

    n_before = 0
    n_after = 0
    for slug, club in store.items():
        sessions = club.get("sessions") or {}
        if not isinstance(sessions, dict):
            continue
        n_before += len(sessions)
        keep = {}
        for sid, s in sessions.items():
            d = (s or {}).get("date") or ""
            if min_date <= d <= max_date:
                keep[sid] = s
        club["sessions"] = keep
        n_after += len(keep)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, separators=(",", ":"))

    size_after = os.path.getsize(path)
    pct = 100 * (1 - size_after / size_before) if size_before else 0
    print(f"  ✂️  {path} : {n_before:,} → {n_after:,} sessions  ({size_before/1e6:.1f} MB → {size_after/1e6:.1f} MB, -{pct:.0f}%)")


def main():
    print(f"Pruning padel stores : window = [today-{WINDOW_DAYS}d ; today+{WINDOW_FUTURE_DAYS}d]")
    for s in STORES:
        prune_one(s)


if __name__ == "__main__":
    main()
