#!/usr/bin/env python3
"""Recalcule les KPIs de l'étude marché → padel_etude_kpis.json.

+ ajoute un tracker de MATURITÉ DE LA DONNÉE par plateforme :
  fraction de 6 mois écoulée depuis le démarrage du tracking. Permet
  d'afficher dans le dashboard où on en est sur le chemin "data complète
  dans 6 mois".

Sortie consommée par padel_etude.html et padel_insights.html.
"""
import datetime as dt
import gzip
import json
import os
from collections import defaultdict, Counter


# Dates de démarrage du tracking par plateforme (basée sur les premiers commits)
# Doinsport bénéficie de l'historique rétroactif via /clubs/bookings/plannings
# Les 3 autres construisent leur historique uniquement à partir du début du polling
TRACKING_STARTED = {
    'anybuddy':   '2026-05-30',  # 1er commit de scrape Anybuddy
    'urbanpadel': '2026-05-31',
    'doinsport':  '2024-01-01',  # historique disponible rétroactivement
    'playtomic':  '2026-05-31',
}
TARGET_MONTHS = 6  # objectif "vraie data robuste dans 6 mois" pour les 3 plateformes sans historique


def get_source(slug, meta):
    if meta.get('source'): return meta['source']
    if slug.startswith('urbanpadel-'): return 'urbanpadel'
    if slug.startswith('doinsport-'): return 'doinsport'
    if slug.startswith('playtomic-'): return 'playtomic'
    return 'anybuddy'


def maturity(source, today):
    start = dt.date.fromisoformat(TRACKING_STARTED[source])
    elapsed_days = max(0, (today - start).days)
    target_days = TARGET_MONTHS * 30
    if source == 'doinsport':
        # Doinsport a déjà l'historique rétroactif → 100% dès le départ
        return {'days_elapsed': elapsed_days, 'target_days': target_days,
                'pct': 100, 'note': 'historique rétroactif disponible'}
    pct = min(100, round(100 * elapsed_days / target_days))
    return {'days_elapsed': elapsed_days, 'target_days': target_days,
            'pct': pct, 'note': f'{elapsed_days}j sur {target_days}j ({TARGET_MONTHS} mois)'}


def main():
    today = dt.date.today()

    # Charge sources
    sources = {}
    if os.path.exists('padel_etude_sources.json'):
        sources = json.load(open('padel_etude_sources.json'))
    live = json.load(open('padel_idf_data.json'))
    history = {}
    if os.path.exists('padel_idf_history.json.gz'):
        with gzip.open('padel_idf_history.json.gz', 'rt') as f:
            history = json.load(f)

    # Aggregations live
    live_sessions = 0
    live_prices_by_dept = defaultdict(list)
    clubs_par_dept = defaultdict(set)
    clubs_par_source = Counter()
    sessions_par_source = Counter()
    for slug, b in live.items():
        meta = b.get('meta') or {}
        src = get_source(slug, meta)
        cp = meta.get('cp', '')
        dept = cp[:2] if len(cp) == 5 else '?'
        clubs_par_dept[dept].add(slug)
        clubs_par_source[src] += 1
        for s in (b.get('sessions') or {}).values():
            live_sessions += 1
            sessions_par_source[src] += 1
            if s.get('prix') and s.get('duree') == 90:
                live_prices_by_dept[dept].append(s['prix'])

    # History stats
    hist_monthly = defaultdict(lambda: defaultdict(int))
    hist_ca_par_club = defaultdict(lambda: {'name': '', 'cp': '', 'bookings': 0, 'ca': 0, 'first': '9999', 'last': '0000'})
    for slug, b in history.items():
        meta = b.get('meta') or {}
        cp = meta.get('cp', '?')
        for s in (b.get('sessions') or {}).values():
            d = s.get('date', '')
            if len(d) >= 7:
                hist_monthly[d[:7]][cp[:2] if len(cp) == 5 else '?'] += 1
            e = hist_ca_par_club[slug]
            e['name'] = meta.get('name', '?')
            e['cp'] = cp
            e['bookings'] += 1
            if s.get('prix'): e['ca'] += s['prix']
            if d < e['first']: e['first'] = d
            if d > e['last']: e['last'] = d

    top_ca = sorted(hist_ca_par_club.items(), key=lambda x: -x[1]['ca'])[:20]

    # INSEE
    insee = sources.get('_insee_idf') or {
        '75': {'nom': 'Paris', 'population': 2102650, 'superficie_km2': 105.4, 'densite_hab_km2': 19956, 'revenu_median_uc': 30750},
        '77': {'nom': 'Seine-et-Marne', 'population': 1452393, 'superficie_km2': 5915, 'densite_hab_km2': 245, 'revenu_median_uc': 24820},
        '78': {'nom': 'Yvelines', 'population': 1448625, 'superficie_km2': 2284, 'densite_hab_km2': 634, 'revenu_median_uc': 28290},
        '91': {'nom': 'Essonne', 'population': 1316939, 'superficie_km2': 1804, 'densite_hab_km2': 729, 'revenu_median_uc': 24370},
        '92': {'nom': 'Hauts-de-Seine', 'population': 1614642, 'superficie_km2': 176, 'densite_hab_km2': 9174, 'revenu_median_uc': 29710},
        '93': {'nom': 'Seine-Saint-Denis', 'population': 1657411, 'superficie_km2': 236, 'densite_hab_km2': 7022, 'revenu_median_uc': 19460},
        '94': {'nom': 'Val-de-Marne', 'population': 1410055, 'superficie_km2': 245, 'densite_hab_km2': 5755, 'revenu_median_uc': 23560},
        '95': {'nom': "Val-d'Oise", 'population': 1255945, 'superficie_km2': 1246, 'densite_hab_km2': 1008, 'revenu_median_uc': 22680},
    }
    dept_summary = {}
    for code, d in insee.items():
        n_clubs = len(clubs_par_dept.get(code, set()))
        prices = live_prices_by_dept.get(code, [])
        pop = d['population']
        dept_summary[code] = {
            'nom': d['nom'], 'pop': pop,
            'superficie_km2': d['superficie_km2'],
            'densite_pop': d['densite_hab_km2'],
            'revenu_median_uc': d.get('revenu_median_uc'),
            'clubs_padel': n_clubs,
            'hab_par_club': pop // n_clubs if n_clubs else None,
            'prix_median_90min': sorted(prices)[len(prices) // 2] if prices else None,
        }

    months_sorted = sorted(hist_monthly.keys())
    monthly_global = [(m, sum(hist_monthly[m].values())) for m in months_sorted]

    # Maturité par plateforme
    maturity_data = {src: maturity(src, today) for src in TRACKING_STARTED}
    # Maturité globale = moyenne pondérée par nb de clubs
    total_clubs = sum(clubs_par_source.values()) or 1
    weighted = sum(maturity_data[s]['pct'] * clubs_par_source[s] for s in clubs_par_source) / total_clubs
    maturity_data['_global_pct'] = round(weighted)

    out = {
        'generated_at': dt.datetime.now().isoformat(),
        'global': {
            'n_clubs': len(live),
            'n_clubs_par_source': dict(clubs_par_source),
            'n_sessions_live_par_source': dict(sessions_par_source),
            'n_sessions_live': live_sessions,
            'n_sessions_history': sum(len(b.get('sessions', {})) for b in history.values()),
            'pop_idf': sum(d['pop'] for d in dept_summary.values()),
        },
        'dept': dept_summary,
        'monthly': monthly_global,
        'top_ca': [{'slug': k, 'name': v['name'], 'cp': v['cp'], 'bookings': v['bookings'],
                    'ca': round(v['ca']), 'first': v['first'][:7], 'last': v['last'][:7]} for k, v in top_ca],
        'weather_correlation': sources.get('_weather', {}).get('correlation_bookings'),
        'weather_period': sources.get('_weather', {}).get('period'),
        'maturity': maturity_data,
    }
    json.dump(out, open('padel_etude_kpis.json', 'w'), indent=1, ensure_ascii=False, default=str)
    print(f"✅ KPIs recalculés : {out['global']['n_clubs']} clubs, "
          f"{out['global']['n_sessions_live']} sessions live, "
          f"{out['global']['n_sessions_history']} sessions historiques")
    print(f"   Maturité globale : {maturity_data['_global_pct']}% du chemin '6 mois data robuste'")
    for src, m in maturity_data.items():
        if src.startswith('_'): continue
        print(f"     · {src:<12} {m['pct']:>3}%   ({m['note']})")


if __name__ == "__main__":
    main()
