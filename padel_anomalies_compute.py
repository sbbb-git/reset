#!/usr/bin/env python3
"""Détection d'anomalies sur la data padel IDF.

Détecte 4 types d'anomalies :
1. ⚠️  Clubs idle : pas vus depuis 24h+ → potentielle fermeture / API down
2. 💰 Prix anormal : prix médian 90min bouge de > 30% vs sa norme 30j
3. 📉 Volume drop : nb bookings 7 derniers jours en chute > 50% vs 30j moyenne
4. 🆕 Nouveau club : premier booking détecté dans les 7 derniers jours

Sortie : padel_anomalies.json (consommé par dashboard) avec horodatage.
Recalcul à chaque passage du workflow (toutes /30 min).
"""
import datetime as dt
import gzip
import json
import os
import statistics
from collections import defaultdict


def get_source(slug, meta):
    if meta.get('source'): return meta['source']
    if slug.startswith('urbanpadel-'): return 'urbanpadel'
    if slug.startswith('doinsport-'): return 'doinsport'
    if slug.startswith('playtomic-'): return 'playtomic'
    return 'anybuddy'


def main():
    now = dt.datetime.now()
    today = now.date()
    live = json.load(open('padel_idf_data.json'))
    history = {}
    if os.path.exists('padel_idf_history.json.gz'):
        with gzip.open('padel_idf_history.json.gz', 'rt') as f:
            history = json.load(f)

    anomalies = {
        'generated_at': now.isoformat(),
        'idle_24h': [],
        'price_shifts': [],
        'volume_drops': [],
        'new_clubs': [],
    }

    # 1. CLUBS IDLE 24h+
    cutoff_24h = now - dt.timedelta(hours=24)
    for slug, b in live.items():
        sess = b.get('sessions') or {}
        meta = b.get('meta') or {}
        src = get_source(slug, meta)
        if not sess: continue
        # Dernier dernier_vu sur les sessions live (non finie)
        last_seen_str = max(
            (s.get('dernier_vu', '') for s in sess.values() if not s.get('finie')),
            default=''
        )
        if not last_seen_str: continue
        try:
            last_seen = dt.datetime.strptime(last_seen_str, '%Y-%m-%d %H:%M')
        except ValueError:
            continue
        if last_seen < cutoff_24h:
            anomalies['idle_24h'].append({
                'slug': slug,
                'source': src,
                'name': meta.get('name') or slug,
                'cp': meta.get('cp'),
                'last_seen': last_seen_str,
                'hours_idle': round((now - last_seen).total_seconds() / 3600, 1),
            })
    anomalies['idle_24h'].sort(key=lambda x: -x['hours_idle'])

    # 2. PRIX SHIFTS : compare prix médian récent (7j) vs prix médian 30j précédents
    # Pour les sources qui ont prix (Doinsport, Playtomic, Anybuddy, UrbanPadel)
    cutoff_7d = (today - dt.timedelta(days=7)).isoformat()
    cutoff_30d = (today - dt.timedelta(days=30)).isoformat()

    for slug, b in live.items():
        sess = list((b.get('sessions') or {}).values())
        meta = b.get('meta') or {}
        # Prix récents 7j vs prix 30j précédents
        prix_recent = [s['prix'] for s in sess if s.get('prix') and s.get('date', '') >= cutoff_7d and s.get('duree') == 90]
        prix_30j = [s['prix'] for s in sess if s.get('prix') and cutoff_30d <= s.get('date', '') < cutoff_7d and s.get('duree') == 90]
        # Compléter avec history pour Doinsport
        if slug in history:
            for s in (history[slug].get('sessions') or {}).values():
                if s.get('prix') and s.get('duree') == 90:
                    d = s.get('date', '')
                    if d >= cutoff_7d:
                        prix_recent.append(s['prix'])
                    elif cutoff_30d <= d < cutoff_7d:
                        prix_30j.append(s['prix'])
        if len(prix_recent) < 3 or len(prix_30j) < 5: continue
        med_recent = statistics.median(prix_recent)
        med_30j = statistics.median(prix_30j)
        if med_30j == 0: continue
        delta_pct = round(100 * (med_recent - med_30j) / med_30j, 1)
        if abs(delta_pct) >= 30:
            anomalies['price_shifts'].append({
                'slug': slug,
                'source': get_source(slug, meta),
                'name': meta.get('name') or slug,
                'cp': meta.get('cp'),
                'prix_30j': round(med_30j, 1),
                'prix_recent': round(med_recent, 1),
                'delta_pct': delta_pct,
                'direction': 'hausse' if delta_pct > 0 else 'baisse',
            })
    anomalies['price_shifts'].sort(key=lambda x: -abs(x['delta_pct']))

    # 3. VOLUME DROPS : compare bookings 7 derniers jours vs moyenne 30j précédents
    # Pour Doinsport surtout (vrai signal de réservation)
    for slug, b in history.items():
        sess = list((b.get('sessions') or {}).values())
        meta = b.get('meta') or {}
        n_recent = sum(1 for s in sess if s.get('date', '') >= cutoff_7d)
        n_30j = sum(1 for s in sess if cutoff_30d <= s.get('date', '') < cutoff_7d)
        if n_30j < 20: continue  # club avec trop peu d'historique
        weekly_avg_30j = n_30j / (30/7)
        if weekly_avg_30j < 1: continue
        delta_pct = round(100 * (n_recent - weekly_avg_30j) / weekly_avg_30j, 1)
        if delta_pct <= -50:
            anomalies['volume_drops'].append({
                'slug': slug,
                'name': meta.get('name'),
                'cp': meta.get('cp'),
                'bookings_7j': n_recent,
                'bookings_moyenne_hebdo_30j': round(weekly_avg_30j, 1),
                'delta_pct': delta_pct,
            })
    anomalies['volume_drops'].sort(key=lambda x: x['delta_pct'])

    # 4. NOUVEAUX CLUBS (premier booking observé dans les 7 derniers jours)
    cutoff_new = (today - dt.timedelta(days=14)).isoformat()  # 14j pour être un peu généreux
    for slug, b in live.items():
        meta = b.get('meta') or {}
        sess = list((b.get('sessions') or {}).values())
        if not sess: continue
        # Plus ancien premier_vu de toutes les sessions du club
        first_seens = [s.get('premier_vu', '') for s in sess if s.get('premier_vu')]
        if not first_seens: continue
        oldest_seen = min(first_seens)
        try:
            oldest_dt = dt.datetime.strptime(oldest_seen, '%Y-%m-%d %H:%M')
        except ValueError:
            continue
        if oldest_dt.date().isoformat() >= cutoff_new:
            anomalies['new_clubs'].append({
                'slug': slug,
                'source': get_source(slug, meta),
                'name': meta.get('name') or slug,
                'cp': meta.get('cp'),
                'first_seen': oldest_seen,
                'days_since': (today - oldest_dt.date()).days,
            })
    anomalies['new_clubs'].sort(key=lambda x: x['days_since'])

    # Résumé
    anomalies['summary'] = {
        'total_anomalies': len(anomalies['idle_24h']) + len(anomalies['price_shifts']) + len(anomalies['volume_drops']),
        'idle_24h': len(anomalies['idle_24h']),
        'price_shifts': len(anomalies['price_shifts']),
        'volume_drops': len(anomalies['volume_drops']),
        'new_clubs_14d': len(anomalies['new_clubs']),
    }

    json.dump(anomalies, open('padel_anomalies.json', 'w'), indent=1, ensure_ascii=False, default=str)
    print(f"✅ Anomalies détectées :")
    s = anomalies['summary']
    print(f"   ⚠️  {s['idle_24h']:>3} clubs idle 24h+")
    print(f"   💰 {s['price_shifts']:>3} prix shifts > 30%")
    print(f"   📉 {s['volume_drops']:>3} volume drops > 50%")
    print(f"   🆕 {s['new_clubs_14d']:>3} nouveaux clubs (14j)")


if __name__ == '__main__':
    main()
