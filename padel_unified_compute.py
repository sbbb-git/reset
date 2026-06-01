#!/usr/bin/env python3
"""Recalcule les clusters cross-plateformes (mapping doublons multi-plateformes).

Fuzzy matching CP + tokens significatifs sur les noms de clubs.
Sortie : padel_club_unified.json (consommé par dashboard + insights).
Exécuté à chaque passage du workflow live (après les 4 scrapers).
"""
import json
import re
from collections import defaultdict


STOP_TOKENS = {
    'padel', 'club', 'tennis', 'sport', 'sports', 'paris', 'centre', 'sportif',
    'complexe', 'de', 'le', 'la', 'les', 'et', 'du', 'd', 'en', 'sur', 'aux',
    'a', 'au', 'des',
}


def norm(s):
    s = (s or '').lower()
    s = re.sub(r'[éèê]', 'e', s)
    s = re.sub(r'[àâ]', 'a', s)
    s = re.sub(r'[ôö]', 'o', s)
    s = re.sub(r'[ûüù]', 'u', s)
    s = re.sub(r'[îï]', 'i', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return ' '.join(s.split())


def get_source(slug, meta):
    if meta.get('source'):
        return meta['source']
    if slug.startswith('urbanpadel-'): return 'urbanpadel'
    if slug.startswith('doinsport-'): return 'doinsport'
    if slug.startswith('playtomic-'): return 'playtomic'
    return 'anybuddy'


def main():
    d = json.load(open('padel_idf_data.json'))

    clubs = []
    for slug, b in d.items():
        meta = b.get('meta') or {}
        src = get_source(slug, meta)
        name = meta.get('name') or slug
        cp = meta.get('cp', '')
        nm_norm = norm(name)
        tokens = set(t for t in nm_norm.split() if len(t) >= 3 and t not in STOP_TOKENS)
        clubs.append({
            'slug': slug, 'src': src, 'name': name, 'cp': cp,
            'tokens': tokens, 'name_norm': nm_norm,
        })

    clusters = []
    used = set()
    for i, c1 in enumerate(clubs):
        if c1['slug'] in used: continue
        cluster = [c1]
        used.add(c1['slug'])
        for c2 in clubs[i+1:]:
            if c2['slug'] in used: continue
            if c2['cp'] != c1['cp']: continue
            common = c1['tokens'] & c2['tokens']
            if common or c1['name_norm'] in c2['name_norm'] or c2['name_norm'] in c1['name_norm']:
                cluster.append(c2)
                used.add(c2['slug'])
        if len({c['src'] for c in cluster}) > 1:
            srcs = sorted({c['src'] for c in cluster})
            clusters.append({
                'unified_id': f'unif_{len(clusters):03d}',
                'cp': c1['cp'],
                'sources': srcs,
                'members': [{'slug': c['slug'], 'source': c['src'], 'name': c['name']} for c in cluster],
                'canonical_name': max(cluster, key=lambda x: len(x['name']))['name'],
            })

    json.dump(clusters, open('padel_club_unified.json', 'w'), indent=1, ensure_ascii=False)
    print(f"✅ {len(clusters)} clusters cross-plateformes recalculés")


if __name__ == "__main__":
    main()
