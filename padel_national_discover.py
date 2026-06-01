#!/usr/bin/env python3
"""Découverte exhaustive des clubs de padel en France métropolitaine.

Stratégie :
- Doinsport + Playtomic + UrbanPadel : catalogues fournis par l'API (complet)
- Anybuddy : test TOUS les ~6000 slugs du sitemap pour activité padel
  (parallèle x30 → ~5 min)
- Pour chaque club, récupération CP + déduction département + métropole.

Sortie :
- padel_national_anybuddy.json
- padel_national_doinsport.json
- padel_national_playtomic.json
- padel_national_urbanpadel.json
- padel_national_clubs.json : catalogue unifié avec metro/dept

Lancement : one-shot via workflow_dispatch (~15 min total).
"""
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = "Mozilla/5.0 Chrome/120"

# Tables des métropoles principales France (sans IDF déjà couverte)
# Chaque métropole = liste de départements adjacents + nom
METROPOLES = {
    'idf':            {'name':'Île-de-France',        'depts': ['75','77','78','91','92','93','94','95']},
    'lyon':           {'name':'Lyon-Rhône-Alpes',     'depts': ['69','01','38','42','07','73','74','26']},
    'marseille':      {'name':'Marseille-PACA',       'depts': ['13','83','84','04','05','06']},
    'bordeaux':       {'name':'Bordeaux-Aquitaine',   'depts': ['33','40','64','24','47']},
    'toulouse':       {'name':'Toulouse-Occitanie',   'depts': ['31','81','82','11','12','32','46','65','66','30','34','48']},
    'lille':          {'name':'Lille-Hauts-de-France','depts': ['59','62','80','02','60']},
    'nantes-rennes':  {'name':'Nantes-Rennes-Bretagne','depts':['44','35','29','56','22','85','49','53','72']},
    'strasbourg':     {'name':'Strasbourg-Grand-Est', 'depts': ['67','68','57','54','55','88','08','10','51','52']},
    'normandie':      {'name':'Normandie',            'depts': ['14','27','50','61','76']},
    'centre':         {'name':'Centre-Val-de-Loire',  'depts': ['18','28','36','37','41','45']},
    'bourgogne':      {'name':'Bourgogne-Franche-Comté','depts': ['21','58','71','89','25','39','70','90']},
    'limousin':       {'name':'Nouvelle-Aquitaine-Sud','depts': ['16','17','19','23','79','86','87']},
    'auvergne':       {'name':'Auvergne',             'depts': ['03','15','43','63']},
    'corse':          {'name':'Corse',                'depts': ['2A','2B']},
}

DEPT_TO_METRO = {}
for metro, conf in METROPOLES.items():
    for d in conf['depts']:
        DEPT_TO_METRO[d] = metro


def fetch(url, timeout=15, retries=2):
    for i in range(retries+1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json,text/html'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode('utf-8', errors='ignore')
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            if i < retries: time.sleep(1.0 * (i+1))
    return None


def metro_for(cp):
    if not cp: return None
    dept = cp[:2] if len(cp) == 5 else (cp[:3] if len(cp) >= 4 and cp[:3] in ('200', '201') else None)
    # Corse 20XXX : 2A si CP commence par 200 ou 201, 2B sinon
    if dept and dept.startswith('20'):
        if cp[:3] in ('200', '201'): dept = '2A'
        else: dept = '2B'
    return DEPT_TO_METRO.get(dept), dept


# ============== DOINSPORT ==============
def discover_doinsport():
    print('=== DOINSPORT national ===')
    all_clubs = []
    page = 1
    while True:
        h = fetch(f'https://api-v3.doinsport.club/clubs?activities[]=padel&country=FR&page={page}', 20)
        if not h: break
        try: d = json.loads(h)
        except: break
        # API peut renvoyer list direct OU dict Hydra
        if isinstance(d, list):
            items = d
            total = None
        else:
            items = d.get('hydra:member', [])
            total = d.get('hydra:totalItems')
        if not items: break
        all_clubs.extend(items)
        if total is not None and len(all_clubs) >= total: break
        page += 1
        if page > 50: break
    print(f'  {len(all_clubs)} clubs Doinsport padel FR')

    rows = []
    for c in all_clubs:
        zip_code = c.get('zipCode') or c.get('postalCode') or ''
        if not zip_code:
            s = json.dumps(c, ensure_ascii=False)
            m = re.search(r'\b(\d{5})\b', s)
            if m: zip_code = m.group(1)
        zip_code = str(zip_code) if zip_code else ''
        if len(zip_code) != 5: continue
        metro, dept = metro_for(zip_code)
        if not metro: continue  # hors France métropolitaine
        rows.append({
            'id': c.get('id'),
            'name': c.get('name'),
            'cp': zip_code, 'city': c.get('city'),
            'dept': dept, 'metro': metro,
            'lat': c.get('latitude'), 'lng': c.get('longitude'),
            'website': c.get('websiteUrl'),
        })
    json.dump(rows, open('padel_national_doinsport.json', 'w'), indent=1, ensure_ascii=False)
    print(f'  → padel_national_doinsport.json ({len(rows)} clubs)')
    return rows


# ============== PLAYTOMIC ==============
def discover_playtomic():
    print('=== PLAYTOMIC national ===')
    h = fetch('https://api.playtomic.io/v1/tenants?country_code=FR&sport_id=PADEL&size=2000', 25)
    if not h: return []
    try: d = json.loads(h)
    except: return []
    rows = []
    for t in d:
        addr = t.get('address') or {}
        if addr.get('country_code') != 'FR': continue
        cp = (addr.get('postal_code') or '').strip()
        name = (t.get('tenant_name') or '').lower()
        if 'inactive' in name: continue
        if len(cp) != 5: continue
        metro, dept = metro_for(cp)
        if not metro: continue
        rows.append({
            'tenant_id': t['tenant_id'], 'name': t.get('tenant_name'),
            'cp': cp, 'city': addr.get('city'),
            'dept': dept, 'metro': metro,
            'lat': (addr.get('coordinate') or {}).get('lat'),
            'lng': (addr.get('coordinate') or {}).get('lon'),
        })
    json.dump(rows, open('padel_national_playtomic.json', 'w'), indent=1, ensure_ascii=False)
    print(f'  → padel_national_playtomic.json ({len(rows)} clubs)')
    return rows


# ============== URBANPADEL ==============
def discover_urbanpadel():
    print('=== URBANPADEL national ===')
    h = fetch('https://myurban.fr/api/read/us/centers', 15)
    if not h: return []
    centers = json.loads(h).get('data', [])
    bk_resp = fetch('https://myurban.fr/api/read/centers/bookable?type=padel', 15)
    padel_ids = set()
    if bk_resp:
        try:
            for c in json.loads(bk_resp).get('data', []):
                for rt in (c.get('resourceTypes') or []):
                    if 'padel' in (rt.get('value') or '').lower():
                        padel_ids.add(c.get('key'))
                        break
        except: pass
    rows = []
    for c in centers:
        if c.get('id') not in padel_ids: continue
        addr = c.get('address') or ''
        m = re.search(r'\b(\d{5})\b', addr)
        cp = m.group(1) if m else ''
        if not cp: continue
        metro, dept = metro_for(cp)
        if not metro: continue
        rows.append({
            'id': c['id'], 'name': c['name'], 'cp': cp, 'address': addr,
            'dept': dept, 'metro': metro,
            'lat': c.get('latitude'), 'lng': c.get('longitude'),
        })
    json.dump(rows, open('padel_national_urbanpadel.json', 'w'), indent=1, ensure_ascii=False)
    print(f'  → padel_national_urbanpadel.json ({len(rows)} clubs)')
    return rows


# ============== ANYBUDDY ==============
# Le plus coûteux : tester tous les slugs du sitemap
def discover_anybuddy():
    print('=== ANYBUDDY national (test tous les slugs FR ~5 min) ===')
    xml = fetch('https://www.anybuddyapp.com/sitemap-clubs-fr.xml', 25)
    if not xml: return []
    all_slugs = sorted(set(re.findall(r'<loc>https://www\.anybuddyapp\.com/fr/club/([a-z0-9-]+)</loc>', xml)))
    print(f'  sitemap : {len(all_slugs)} clubs')

    def test_slug(slug):
        h = fetch(f'https://www.anybuddyapp.com/api/v1/availabilities?clubSlug={slug}&dateFrom=2026-06-03T00:00&dateTo=2026-06-06T23:59&activity=padel', 8, retries=1)
        if not h: return slug, 0
        try:
            d = json.loads(h)
            return slug, len(d.get('data', []))
        except: return slug, 0

    actifs = []
    done = 0
    with ThreadPoolExecutor(max_workers=30) as ex:
        for slug, n in ex.map(test_slug, all_slugs):
            done += 1
            if n > 0: actifs.append((slug, n))
            if done % 500 == 0:
                print(f'    {done}/{len(all_slugs)} testés, {len(actifs)} actifs')
    print(f'  {len(actifs)} clubs Anybuddy actifs en padel')

    # Géolocalisation : extraire CP depuis HTML page club
    def get_cp_and_addr(slug):
        h = fetch(f'https://www.anybuddyapp.com/fr/club/{slug}', 10, retries=1)
        if not h: return slug, None, None
        m = re.search(r'\b(\d{5})\b', h)
        # Aussi extraire un nom propre depuis title
        title_m = re.search(r'<title>([^<]+)</title>', h)
        return slug, (m.group(1) if m else None), (title_m.group(1).split('|')[0].strip() if title_m else None)

    rows = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        futs = {ex.submit(get_cp_and_addr, s): (s, n) for s, n in actifs}
        for f in as_completed(futs):
            slug, n = futs[f]
            _, cp, name = f.result()
            if not cp or len(cp) != 5: continue
            metro, dept = metro_for(cp)
            if not metro: continue
            rows.append({
                'slug': slug, 'cp': cp, 'name': name or slug,
                'dept': dept, 'metro': metro,
                'creneaux_test': n,
            })
    json.dump(rows, open('padel_national_anybuddy.json', 'w'), indent=1, ensure_ascii=False)
    print(f'  → padel_national_anybuddy.json ({len(rows)} clubs géo-validés)')
    return rows


# ============== UNIFICATION ==============
def unify():
    """Génère le catalogue national unifié avec dédup par metro/cp/nom."""
    print('\n=== UNIFICATION CATALOGUE NATIONAL ===')
    catalog = {}  # key = unique signature → club entries from each source

    for src_file, src_name, get_slug in [
        ('padel_national_anybuddy.json',  'anybuddy',  lambda c: c['slug']),
        ('padel_national_doinsport.json', 'doinsport', lambda c: f"doinsport-{c['id']}"),
        ('padel_national_playtomic.json', 'playtomic', lambda c: f"playtomic-{c['tenant_id'][:8]}"),
        ('padel_national_urbanpadel.json','urbanpadel',lambda c: f"urbanpadel-{c['id']}"),
    ]:
        try:
            rows = json.load(open(src_file))
        except FileNotFoundError:
            continue
        for c in rows:
            slug = get_slug(c)
            catalog[slug] = {
                'slug': slug, 'source': src_name,
                'name': c.get('name'), 'cp': c.get('cp'),
                'city': c.get('city'), 'dept': c.get('dept'),
                'metro': c.get('metro'),
                'lat': c.get('lat'), 'lng': c.get('lng'),
                'raw': c,
            }
    print(f'  {len(catalog)} clubs uniques par signature source/id')

    # Stats agrégées
    from collections import Counter
    by_metro = Counter()
    by_source = Counter()
    for c in catalog.values():
        if c.get('metro'): by_metro[c['metro']] += 1
        by_source[c['source']] += 1

    json.dump(list(catalog.values()), open('padel_national_clubs.json', 'w'), indent=1, ensure_ascii=False)
    print(f'  → padel_national_clubs.json ({len(catalog)} entrées)')
    print('\n📊 Répartition par métropole :')
    for m, n in by_metro.most_common():
        nm = METROPOLES[m]['name']
        print(f'  {m:<16} ({nm}) : {n} clubs (toutes sources confondues, avec doublons inter-sources)')
    print('\n📊 Par source :')
    for s, n in by_source.most_common():
        print(f'  {s:<12} {n} entrées')

    return catalog


if __name__ == '__main__':
    t0 = time.time()
    discover_doinsport()
    discover_playtomic()
    discover_urbanpadel()
    discover_anybuddy()
    unify()
    print(f'\n✅ Découverte nationale complète en {(time.time()-t0)/60:.1f} min')
