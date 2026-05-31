#!/usr/bin/env python3
"""Auto-discovery hebdomadaire des nouveaux clubs padel IDF.

Pour chaque plateforme, recompare son catalogue actuel avec ce qu'on a en
base et ajoute les nouveaux clubs au catalogue tracké. Le marché padel
grandit ~30%/an donc on s'attend à 1-3 nouveaux clubs IDF par mois.

À lancer en workflow GitHub Action hebdomadaire (lundi 5h).
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
IDF_DEPTS = {'75','77','78','91','92','93','94','95'}


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def discover_anybuddy(existing_slugs):
    """Re-scrape sitemap + test chaque slug candidat IDF."""
    try:
        xml = _get('https://www.anybuddyapp.com/sitemap-clubs-fr.xml', timeout=20)
    except Exception as e:
        print(f"❌ sitemap Anybuddy : {e}", file=sys.stderr)
        return []
    all_slugs = sorted(set(re.findall(r'<loc>https://www\.anybuddyapp\.com/fr/club/([a-z0-9-]+)</loc>', xml)))
    # Tokens IDF même large
    IDF = ('paris boulogne neuilly asni courbevoie levallois issy vincennes saint-cloud saint-mande '
           'st-mande saint-denis montreuil champigny fontenay nogent joinville creteil créteil nanterre '
           'rueil puteaux antony clichy clamart meudon versailles saint-germain marly evry cergy pontoise '
           'argenteuil massy orsay palaiseau cormeilles noisy bobigny aubervilliers saint-ouen drancy epinay '
           'le-perreux colombes gennevilliers ivry vitry villejuif sucy bry champs chelles torcy lognes '
           'noisiel bussy claye-souilly melun fontainebleau trappes maurepas elancourt guyancourt montigny '
           'poissy conflans plaisir rambouillet mantes magny aulnay stains villepinte sevran tremblay pantin '
           'romainville lilas bagnolet montrouge chatillon malakoff sceaux cachan arcueil gentilly kremlin '
           'charenton alfortville maisons-alfort saint-maur bonneuil valenton orly choisy thiais fresnes '
           'chevilly rungis wissous bagneux chaville sevres garches vaucresson marnes jouy buc chevreuse '
           'gif bures villebon champlan nozay brunoy montgeron crosne yerres grigny viry juvisy athis '
           'paray fleury sainte-genevieve ris-orangis corbeil mennecy milly etampes dourdan saulx bondy '
           'montfermeil villemomble rosny plessis chennevieres limeil saint-maurice nemours provins coulommiers '
           'meaux lagny crecy tournan brie-comte-robert combs moissy savigny cesson vert-saint-denis lieusaint '
           'nandy mee dammarie marly chatou vesinet croissy montesson carrieres maisons-laffitte sartrouville '
           'herblay franconville enghien montmorency deuil sannois bezons houilles saint-leu domont ecouen '
           'gonesse villiers-le-bel sarcelles garges goussainville persan beaumont chambly isle-adam villabe '
           'idf seine-saint-denis val-de-marne hauts-de-seine val-d-oise yvelines essonne seine-et-marne').split()
    BRANDS = ('casa first padel-attitude padel-up padel-shot padelshot 4padel 4-padel le-five sportfield '
              'aldea trinquet aquaboulevard padelisto').split()
    candidates = sorted({s for s in all_slugs if any(t in s.lower() for t in IDF) or any(t in s.lower() for t in BRANDS)})
    new_candidates = [s for s in candidates if s not in existing_slugs]
    print(f"Anybuddy : {len(candidates)} candidats IDF dans sitemap, {len(new_candidates)} non encore trackés")

    def test(slug):
        try:
            d = json.loads(_get(f'https://www.anybuddyapp.com/api/v1/availabilities?'
                                f'clubSlug={slug}&dateFrom=2026-06-02T00:00&dateTo=2026-06-05T23:59&activity=padel'))
            return slug, len(d.get('data', []))
        except Exception:
            return slug, 0

    def get_cp(slug):
        try:
            html = _get(f'https://www.anybuddyapp.com/fr/club/{slug}', timeout=10)
            m = re.search(r'\b(\d{5})\b', html)
            return m.group(1) if m else None
        except Exception:
            return None

    actifs = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        for slug, n in ex.map(test, new_candidates):
            if n > 0:
                actifs.append((slug, n))

    new_idf = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(get_cp, s): (s, n) for s, n in actifs}
        for f in as_completed(futs):
            slug, n = futs[f]
            cp = f.result()
            if cp and cp[:2] in IDF_DEPTS:
                new_idf.append({'slug': slug, 'cp': cp, 'creneaux_test': n, 'discovered_at': dt.date.today().isoformat()})
    return new_idf


def discover_doinsport(existing_uuids):
    """Récupère tout le catalogue padel FR et filtre par CP IDF."""
    all_clubs = []
    page = 1
    while True:
        try:
            d = json.loads(_get(f'https://api-v3.doinsport.club/clubs?activities[]=padel&country=FR&page={page}'))
        except Exception as e:
            print(f"❌ Doinsport catalogue : {e}", file=sys.stderr); break
        items = d.get('hydra:member', [])
        all_clubs.extend(items)
        if not items or len(all_clubs) >= d.get('hydra:totalItems', 0): break
        page += 1
        if page > 50: break
    new_idf = []
    for c in all_clubs:
        if c.get('id') in existing_uuids: continue
        zip_code = c.get('zipCode') or c.get('postalCode') or ''
        if not zip_code:
            s = json.dumps(c, ensure_ascii=False)
            m = re.search(r'\b(\d{5})\b', s)
            if m: zip_code = m.group(1)
        if str(zip_code)[:2] in IDF_DEPTS and len(str(zip_code)) == 5:
            new_idf.append({
                'name': c.get('name'), 'cp': str(zip_code), 'city': c.get('city'),
                'id': c.get('id'), 'addr': c.get('address'),
                'lat': c.get('latitude'), 'lng': c.get('longitude'),
                'website': c.get('websiteUrl'),
                'discovered_at': dt.date.today().isoformat(),
            })
    print(f"Doinsport : {len(all_clubs)} clubs padel FR au total, {len(new_idf)} nouveaux en IDF")
    return new_idf


def discover_playtomic(existing_tenant_ids):
    """Récupère le catalogue padel FR Playtomic."""
    try:
        d = json.loads(_get('https://api.playtomic.io/v1/tenants?country_code=FR&sport_id=PADEL&size=2000'))
    except Exception as e:
        print(f"❌ Playtomic : {e}", file=sys.stderr); return []
    new_idf = []
    for t in d:
        if t.get('tenant_id') in existing_tenant_ids: continue
        addr = t.get('address') or {}
        if addr.get('country_code') != 'FR': continue
        cp = (addr.get('postal_code') or '').strip()
        name = (t.get('tenant_name') or '').lower()
        if 'inactive' in name: continue
        if cp[:2] in IDF_DEPTS and len(cp) == 5:
            new_idf.append({
                'tenant_id': t['tenant_id'], 'name': t.get('tenant_name'),
                'cp': cp, 'city': addr.get('city'),
                'lat': (addr.get('coordinate') or {}).get('lat'),
                'lng': (addr.get('coordinate') or {}).get('lon'),
                'status': t.get('playtomic_status'),
                'discovered_at': dt.date.today().isoformat(),
            })
    print(f"Playtomic : {len(d)} clubs padel FR au total, {len(new_idf)} nouveaux en IDF")
    return new_idf


def main():
    # Charge les catalogues actuels
    ab_existing = {c['slug'] for c in json.load(open('padel_idf_clubs.json'))}
    do_existing = {c['id'] for c in json.load(open('doinsport_idf_clubs.json'))}
    pt_existing = {c['tenant_id'] for c in json.load(open('playtomic_idf_clubs.json'))}

    new_ab = discover_anybuddy(ab_existing)
    new_do = discover_doinsport(do_existing)
    new_pt = discover_playtomic(pt_existing)

    total = len(new_ab) + len(new_do) + len(new_pt)
    print(f"\n🆕 TOTAL : {total} nouveaux clubs IDF découverts cette semaine")

    if new_ab:
        ab = json.load(open('padel_idf_clubs.json'))
        ab.extend(new_ab)
        ab.sort(key=lambda x: x['slug'])
        json.dump(ab, open('padel_idf_clubs.json', 'w'), indent=1, ensure_ascii=False)
        print(f"  + {len(new_ab)} Anybuddy → padel_idf_clubs.json")
        for c in new_ab: print(f"    • {c['slug']} (CP {c['cp']})")
    if new_do:
        do = json.load(open('doinsport_idf_clubs.json'))
        do.extend(new_do)
        do.sort(key=lambda x: x['cp'])
        json.dump(do, open('doinsport_idf_clubs.json', 'w'), indent=1, ensure_ascii=False)
        print(f"  + {len(new_do)} Doinsport → doinsport_idf_clubs.json")
        for c in new_do: print(f"    • {c['name']} (CP {c['cp']})")
    if new_pt:
        pt = json.load(open('playtomic_idf_clubs.json'))
        pt.extend(new_pt)
        pt.sort(key=lambda x: x['cp'])
        json.dump(pt, open('playtomic_idf_clubs.json', 'w'), indent=1, ensure_ascii=False)
        print(f"  + {len(new_pt)} Playtomic → playtomic_idf_clubs.json")
        for c in new_pt: print(f"    • {c['name']} (CP {c['cp']})")

    if total == 0:
        print("  Catalogues à jour, rien à faire.")


if __name__ == "__main__":
    main()
