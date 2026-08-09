#!/usr/bin/env python3
"""Scrape unifié des 44 studios Pilates en extension.

Lit pilates_extension_resolved.json (produit par pilates_extension_discover.py)
et applique le bon engine de scrape par plateforme :

- bsport            → bsport_scrape.run() avec company_id
- mindbody (HTTP)   → engine banote-style (pour widgets BW classiques)
- mindbody_healcode → engine_mindbody_healcode.fetch_sessions()
- mindbody (SPA)    → engine reformation_evocore (Playwright)
- arketa          → engine snakeandtwist-style
- resamania       → engine_resamania (API Resamania II + proxys publics)
- sportigo        → engine_sportigo (planning public du front <slug>.sportigo.club)
- clubready       → engine_clubready (Club Pilates FR : c'est du Glofox, pas du
                    ClubReady — voir l'en-tête de engine_clubready.py)
- lefive          → engine_lefive (foot 5 / basket : créneaux de TERRAIN, pas
                    des cours — store_court_slots au lieu de
                    store_planning_sessions)
- urbansoccer     → engine_urbansoccer (idem ; même backend que
                    urbanpadel_scrape.py, en-tête `activity` différent)
- extraclub       → identifié mais verrouillé (Hoops Factory : person_id d'un
                    compte client + X-WSSE de service) — signalé, pas scrapé
- wordpress       → TODO case-by-case
- unknown         → skip avec log

Deux modèles de données cohabitent donc dans ce dispatch :
un cours collectif a une capacité et des inscrits (store_planning_sessions) ;
un terrain de foot 5 a N terrains libres sur un créneau (store_court_slots).
Ne pas mélanger les deux : c'est ce qui rendait les catégories Foot/Hoops
inexploitables tant qu'on cherchait à les faire entrer dans le moule Pilates.

Chaque brand produit son propre <key>_data.json + <key>.html via le moteur
correspondant. L'agrégat Pilates IDF (pilates_idf_compute.py) inclura
automatiquement ces brands au prochain run.
"""
import importlib
import json
import os
import sys
import traceback

RESOLVED = "pilates_extension_resolved.json"
BRANDS_CFG = "pilates_extension_brands.json"


def load_or_die(path):
    if not os.path.exists(path):
        print(f"❌ {path} manquant — lance pilates_extension_discover.py d'abord", file=sys.stderr)
        sys.exit(1)
    return json.load(open(path, encoding="utf-8"))


def scrape_bsport(key, label, company_id):
    """Réutilise bsport_scrape.run avec un cfg minimal."""
    import bsport_scrape
    cfg = {
        "key": key,
        "brand": label.upper(),
        "companies": [int(company_id)],
        "host": "extension",
        "store": f"{key}_data.json",
        "html": f"{key}.html",
        "csv": f"{key}_seances.csv",
        "price": 30,
        "accent": "#b07ff0",
        "accent2": "#d4b8ff",
        "methode": f"<b>{label}</b> — extension auto-discovered (bsport company {company_id})",
    }
    bsport_scrape.run(cfg)


def store_mindbody_sessions(key, label, sessions):
    """Écrit les séances Mindbody brutes dans <key>_data.json (compatible heatmap).

    Format d'entrée : celui de banote_fetch.fetch_all() / du nouvel engine
    healcode — {id, start, end, cours, coach, lieu, cart}. Mindbody n'expose
    ni la capacité ni le nombre d'inscrits, on ne dérive qu'un STATUT du
    bouton panier (comme banote / Sense-Club).
    """
    import datetime as dt
    from zoneinfo import ZoneInfo
    import safestore
    PARIS = ZoneInfo("Europe/Paris")
    JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

    now = dt.datetime.now(PARIS)
    store_path = f"{key}_data.json"
    store = safestore.load(store_path)
    kept = 0
    for s in sessions:
        sid = str(s.get("id") or "")
        if not sid:
            continue
        try:
            sdt = dt.datetime.fromisoformat(s["start"]).replace(tzinfo=PARIS)
        except (ValueError, KeyError, TypeError):
            continue
        cart = (s.get("cart") or "").lower()
        statut = "complet" if any(k in cart for k in ("waitlist", "complet", "full", "attente")) else \
                 "disponible" if any(k in cart for k in ("book", "reserv", "réserv")) else "inconnu"
        cap = 12
        store[sid] = {
            "id": sid,
            "date": sdt.date().isoformat(),
            "jour": JOURS_FR[sdt.weekday()],
            "heure": sdt.strftime("%H:%M"),
            "fin": s.get("end", "")[:16][-5:] if s.get("end") else "",
            "lieu": (s.get("lieu") or label),
            "cours": (s.get("cours") or "").strip(),
            "coach": (s.get("coach") or "").strip(),
            "capacite": cap if statut in ("complet", "disponible") else 0,
            "presents": cap if statut == "complet" else 0,
            "finie": now >= sdt - dt.timedelta(minutes=15),
            "statut": statut,
            "releve": now.strftime("%Y-%m-%d %H:%M"),
        }
        kept += 1
    safestore.save(store, store_path)
    print(f"  ← {kept} séances vues, {len(store)} en base ({key})")


def scrape_mindbody_http(key, label, widget_id):
    """Engine HTTP pour widgets BW classiques (banote / le33foch-style).

    On passe par engine_mindbody_healcode : même endpoint load_markup, mais
    l'engine balaie plusieurs fenêtres (2 semaines par appel), espace ses
    requêtes pour ne pas déclencher le throttling Mindbody, et clé les séances
    sur le ClassID stable plutôt que sur l'id de DOM. Repli sur le33foch_fetch
    si l'engine ne rend rien, pour ne rien perdre en cas de régression.
    """
    sessions = []
    try:
        import engine_mindbody_healcode as healcode
        sessions = healcode.fetch_sessions(None, widget_id=widget_id, lieu=label)
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  engine healcode {key} échoué : {e}", file=sys.stderr)

    if not sessions:
        try:
            import le33foch_fetch
            original_widget = le33foch_fetch.WIDGET_ID
            original_lieu = le33foch_fetch.LIEU_FALLBACK
            le33foch_fetch.WIDGET_ID = widget_id
            le33foch_fetch.LIEU_FALLBACK = label
            try:
                sessions = le33foch_fetch.fetch_all()
            finally:
                le33foch_fetch.WIDGET_ID = original_widget
                le33foch_fetch.LIEU_FALLBACK = original_lieu
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️  fetch Mindbody {key} échoué : {e}", file=sys.stderr)

    if not sessions:
        print(f"  → 0 séances pour {key} (peut nécessiter Playwright)", file=sys.stderr)
        return
    store_mindbody_sessions(key, label, sessions)


def scrape_mindbody_healcode(key, label, res):
    """Engine healcode : <healcode-widget data-type="schedules" ...>.

    Le planning s'adresse par widget_id (pas par site_id) ; l'engine résout
    le widget via son registre, l'entrée resolved, ou une discovery sur le
    site de la marque. Renvoie [] sans exception si la marque n'est pas (ou
    plus) joignable en healcode — le motif part sur stderr.
    """
    import engine_mindbody_healcode as healcode
    sessions = healcode.fetch_sessions_for_brand(res, label)
    if not sessions:
        print(f"  → 0 séance pour {key} (voir stderr : widget absent, "
              f"Branded Web V2, ou plateforme changée)", file=sys.stderr)
        return False
    store_mindbody_sessions(key, label, sessions)
    return True


def store_planning_sessions(key, label, sessions):
    """Écrit dans <key>_data.json des séances au format commun des engines.

    Format d'entrée : {id, start, end, cours, coach, lieu} + capacite/presents
    optionnels — celui rendu par engine_resamania, engine_sportigo et
    engine_clubready. `start`/`end` sont des ISO-8601 locaux (Europe/Paris) ;
    un fuseau explicite est respecté s'il est présent.

    capacite/presents à None signifient « la plateforme ne les publie pas » :
    on écrit 0 et un statut "inconnu" plutôt que d'inventer un remplissage.
    """
    import datetime as dt
    from zoneinfo import ZoneInfo
    import safestore
    PARIS = ZoneInfo("Europe/Paris")
    JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

    now = dt.datetime.now(PARIS)
    store_path = f"{key}_data.json"
    store = safestore.load(store_path)
    kept = 0
    for s in sessions:
        sid = str(s.get("id") or "")
        if not sid or not s.get("start"):
            continue
        try:
            sdt = dt.datetime.fromisoformat(s["start"])
        except (ValueError, TypeError):
            continue
        if sdt.tzinfo is None:
            sdt = sdt.replace(tzinfo=PARIS)
        cap = s.get("capacite")
        pres = s.get("presents")
        if cap is None or pres is None:
            statut = s.get("statut") or "inconnu"
        elif pres >= cap:
            statut = "complet"
        else:
            statut = "disponible"
        fin = ""
        if s.get("end"):
            try:
                fin = dt.datetime.fromisoformat(s["end"]).strftime("%H:%M")
            except (ValueError, TypeError):
                fin = ""
        store[sid] = {
            "id": sid,
            "date": sdt.date().isoformat(),
            "jour": JOURS_FR[sdt.weekday()],
            "heure": sdt.strftime("%H:%M"),
            "fin": fin,
            "lieu": (s.get("lieu") or s.get("club") or label),
            "cours": (s.get("cours") or "").strip(),
            "coach": (s.get("coach") or "").strip(),
            "capacite": cap or 0,
            "presents": pres or 0,
            "finie": now >= sdt - dt.timedelta(minutes=15),
            "statut": statut,
            "releve": now.strftime("%Y-%m-%d %H:%M"),
        }
        kept += 1
    safestore.save(store, store_path)
    src = sessions[0].get("source", "?")
    print(f"  ← {kept} séances vues ({src}), {len(store)} en base ({key})")
    return kept


def store_court_slots(key, label, slots):
    """Écrit dans <key>_data.json des créneaux de TERRAIN (foot 5, basket…).

    Modèle différent de store_planning_sessions : ici l'unité n'est pas un cours
    à capacité, c'est un créneau sur lequel il reste N terrains libres. Les deux
    plateformes ne donnent pas le même signal, et le store le reflète :

      · LE FIVE rend la GRILLE COMPLÈTE, créneaux saturés inclus (`free_courts`
        = 0). L'occupation est donc OBSERVÉE : statut "complet" est un fait.
      · URBANSOCCER ne rend que le disponible. L'occupation est DÉDUITE, comme
        pour Anybuddy / UrbanPadel : un créneau vu disponible, pas encore passé,
        et absent du relevé courant a été réservé → statut "reserve".
        D'où vu_dispo / vu_dispo_ce_passage, repris de urbanpadel_scrape.py.

    `capacite` / `presents` sont fournis pour rester consommables par les
    agrégats existants, mais ils reposent sur une ESTIMATION assumée : le
    nombre total de terrains d'un centre n'est publié par aucune des deux API.
    On l'approche par le maximum de terrains simultanément libres jamais
    observé sur le pool (centre × durée × type de terrain). C'est exact dès
    qu'une heure creuse laisse le centre entièrement libre ; sinon la valeur
    minore le parc réel, et comme elle ne redescend jamais, elle le majorerait
    après une fermeture de terrain. La donnée réellement MESURÉE est
    `terrains_libres` — c'est elle qu'il faut utiliser pour toute analyse
    sérieuse, `capacite`/`presents` ne sont qu'une commodité d'affichage.
    """
    import datetime as dt
    from zoneinfo import ZoneInfo
    import safestore
    PARIS = ZoneInfo("Europe/Paris")
    JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

    now = dt.datetime.now(PARIS)
    store_path = f"{key}_data.json"
    store = safestore.load(store_path)

    def pool_of(source, center_id, duree, court_type):
        # UrbanSoccer sépare des pools distincts (Intérieur / Extérieur) qu'il
        # ne faut pas additionner ; Le Five expose un seul pool par centre et
        # ne nomme même pas le type quand le créneau est saturé.
        return (f"{source}|{center_id}|{duree}|"
                f"{court_type if source == 'urbansoccer' else ''}")

    # Plafonds déjà connus, pour que l'estimation ne redescende jamais.
    pool_max = {}
    for v in store.values():
        p = v.get("pool")
        if p:
            pool_max[p] = max(pool_max.get(p, 0),
                              v.get("terrains_total_estime") or 0)

    # 1) tout ce qui n'est ni passé ni revu ce coup-ci est candidat "réservé"
    for v in store.values():
        if v.get("finie"):
            continue
        v["vu_dispo_ce_passage"] = False

    # 2) plafond de ce passage
    for s in slots:
        free = s.get("free_courts") or 0
        p = pool_of(s["source"], s["center_id"], s["duration"], s.get("court_type"))
        pool_max[p] = max(pool_max.get(p, 0), free)

    kept = 0
    for s in slots:
        sid = str(s.get("id") or "")
        if not sid or not s.get("start"):
            continue
        try:
            sdt = dt.datetime.fromisoformat(s["start"])
        except (ValueError, TypeError):
            continue
        if sdt.tzinfo is None:
            sdt = sdt.replace(tzinfo=PARIS)
        duree = int(s.get("duration") or 60)
        edt = sdt + dt.timedelta(minutes=duree)
        free = s.get("free_courts")
        free = 0 if free is None else int(free)
        p = pool_of(s["source"], s["center_id"], duree, s.get("court_type"))
        total = max(pool_max.get(p, 0), free)
        prev = store.get(sid, {})
        store[sid] = {
            "id": sid,
            "date": sdt.date().isoformat(),
            "jour": JOURS_FR[sdt.weekday()],
            "heure": sdt.strftime("%H:%M"),
            "fin": edt.strftime("%H:%M"),
            "start": s["start"],
            "duree": duree,
            "lieu": s.get("center_name") or label,
            "cp": s.get("cp"),
            "center_id": s.get("center_id"),
            "sport": s.get("sport"),
            "terrain": s.get("court_type") or "",
            "terrains": s.get("courts") or [],
            "terrains_libres": free,
            "terrains_total_estime": total,
            "pool": p,
            "prix": s.get("price") if s.get("price") is not None else prev.get("prix"),
            "capacite": total,
            "presents": max(total - free, 0),
            "statut": "complet" if free == 0 else "disponible",
            "vu_dispo": bool(prev.get("vu_dispo")) or free > 0,
            "vu_dispo_ce_passage": True,
            "premier_vu": prev.get("premier_vu") or now.strftime("%Y-%m-%d %H:%M"),
            "dernier_vu": now.strftime("%Y-%m-%d %H:%M"),
            "finie": now >= edt,
            "releve": now.strftime("%Y-%m-%d %H:%M"),
            "source": s.get("source"),
        }
        kept += 1

    # 3) disparitions = réservations (seul signal disponible côté UrbanSoccer)
    disparus = 0
    for v in store.values():
        try:
            sdt = dt.datetime.fromisoformat(v["start"])
        except (ValueError, KeyError, TypeError):
            continue
        if sdt.tzinfo is None:
            sdt = sdt.replace(tzinfo=PARIS)
        if now >= sdt + dt.timedelta(minutes=int(v.get("duree") or 60)):
            v["finie"] = True
        if (not v.get("finie") and not v.get("vu_dispo_ce_passage")
                and v.get("vu_dispo") and v.get("statut") == "disponible"):
            v["statut"] = "reserve"
            v["terrains_libres"] = 0
            v["presents"] = v.get("capacite") or 0
            disparus += 1

    safestore.save(store, store_path)
    complets = sum(1 for s in slots if (s.get("free_courts") or 0) == 0)
    print(f"  ← {kept} créneaux vus ({complets} complets, {disparus} disparus), "
          f"{len(store)} en base ({key})")
    return kept


def scrape_lefive(key, label, res):
    """Engine Le Five (backend propriétaire SPLF) — cf. engine_lefive.py.

    Le centre s'adresse par center_id ; la clé de marque suffit quand elle est
    au registre de l'engine. Un centre fermé ou un sport non proposé lève
    LeFiveError : on le dit, on ne rend pas un store vide silencieux.
    """
    import engine_lefive as lefive

    ident = key if key in lefive.SOURCES else (res.get("center_id") or key)
    try:
        slots = lefive.fetch_slots(ident, days=lefive.HORIZON_DEFAULT,
                                   sport=res.get("sport"))
    except lefive.LeFiveError as e:
        print(f"  ⚠️  lefive {key} échoué : {e}", file=sys.stderr)
        return
    if not slots:
        print(f"  → 0 créneau pour {key} (lefive — centre joignable mais "
              f"grille vide)", file=sys.stderr)
        return
    store_court_slots(key, label, slots)


def scrape_urbansoccer(key, label, res):
    """Engine UrbanSoccer (myurban.fr) — cf. engine_urbansoccer.py.

    Même backend que urbanpadel_scrape.py, en-tête `activity: 1` au lieu de 2.
    """
    import engine_urbansoccer as urbansoccer

    ident = key if key in urbansoccer.SOURCES else (res.get("center_id") or key)
    try:
        slots = urbansoccer.fetch_slots(ident, days=urbansoccer.HORIZON_DEFAULT,
                                        sport=res.get("sport"))
    except urbansoccer.UrbanSoccerError as e:
        print(f"  ⚠️  urbansoccer {key} échoué : {e}", file=sys.stderr)
        return
    if not slots:
        print(f"  → 0 créneau pour {key} (urbansoccer — aucun terrain libre "
              f"sur l'horizon)", file=sys.stderr)
        return
    store_court_slots(key, label, slots)


def scrape_resamania(key, label, res):
    """Engine Resamania II (Cercles de la Forme, OZEN HIT, ...).

    engine_resamania.fetch_sessions() tente l'API `class_events` puis, si son
    scope anonyme l'interdit (cas de cdf), retombe sur le proxy public du site
    vitrine. Il rend {id, start, end, cours, coach, lieu, capacite, presents} ;
    capacite/presents ne sont renseignés que par le chemin API authentifié —
    via un proxy ils valent None, et le store écrit 0 (statut "inconnu").
    """
    import engine_resamania as resamania

    slug = key if key in resamania.SOURCES else (res.get("slug") or key)
    try:
        sessions = resamania.fetch_sessions(slug, days=14)
    except resamania.ResamaniaAuthRequired as e:
        print(f"⏳ {key:30s} resamania — {e}", file=sys.stderr)
        return
    except resamania.ResamaniaError as e:
        print(f"  ⚠️  resamania {key} échoué : {e}", file=sys.stderr)
        return

    if not sessions:
        print(f"  → 0 séance pour {key} (resamania)", file=sys.stderr)
        return
    store_planning_sessions(key, label, sessions)


def scrape_sportigo(key, label, res):
    """Engine Sportigo (tenants <slug>.sportigo.club).

    Le planning public rend capacite ET presents — rare, et exploitable tel
    quel par la heatmap. Un tenant joignable qui ne publie rien rend [] : ce
    n'est pas une erreur, on le dit et on passe.
    """
    import engine_sportigo as sportigo

    ident = (key if key in sportigo.SOURCES else
             (res.get("slug") or res.get("booking_url") or res.get("url") or key))
    try:
        sessions = sportigo.fetch_sessions(ident, days=21)
    except sportigo.SportigoError as e:
        print(f"  ⚠️  sportigo {key} échoué : {e}", file=sys.stderr)
        return
    if not sessions:
        print(f"  → 0 séance pour {key} (sportigo — tenant joignable mais "
              f"planning vide sur 21 j)", file=sys.stderr)
        return
    store_planning_sessions(key, label, sessions)


def scrape_clubready(key, label, res):
    """Engine « ClubReady » = Glofox pour Club Pilates France.

    Le tag `clubready` du discover est un abus de langage hérité de la
    franchise Xponential : en France ces clubs tournent sur Glofox, dont le
    portail public rend capacite/presents/liste d'attente. Voir l'en-tête de
    engine_clubready.py.

    Une marque taguée `clubready` à tort (le détecteur matche sur la chaîne
    "club-pilates") ressort ici en erreur explicite, pas en séances vides.
    """
    import engine_clubready as clubready

    ident = (key if key in clubready.SOURCES else
             (res.get("branch_id") or res.get("club_id")
              or res.get("booking_url") or res.get("url") or key))
    try:
        sessions = clubready.fetch_sessions(ident, days=42)
    except clubready.ClubReadyAuthRequired as e:
        print(f"⏳ {key:30s} clubready/glofox — {e}", file=sys.stderr)
        return
    except clubready.ClubReadyError as e:
        print(f"  ⚠️  clubready {key} échoué : {e}", file=sys.stderr)
        return
    if not sessions:
        print(f"  → 0 séance pour {key} (clubready/glofox)", file=sys.stderr)
        return
    store_planning_sessions(key, label, sessions)


def _engine_brand_keys(module_name):
    """Clés de marque connues du registre d'un engine (vide si import KO).

    Permet de router une marque que la discovery n'a pas su classer mais dont
    l'engine, lui, connaît l'identifiant technique.
    """
    try:
        mod = importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 — un engine cassé ne doit pas tuer le run
        return set()
    return set(getattr(mod, "SOURCES", {}) or {})


def scrape_brand(key, brand_cfg, resolved):
    label = brand_cfg.get("label") or key
    res = resolved.get(key) or {}
    platform = res.get("platform")

    status = res.get("status", "")

    # Statuts terminaux : skip silencieux
    if status in ("skip", "defunct"):
        return
    if platform in ("defunct", "not_live"):
        return

    # Bsport résolu → scrape immédiat
    if platform == "bsport" and res.get("company_id") and res["company_id"] != "TODO":
        print(f"→ {key:30s} bsport company={res['company_id']}")
        scrape_bsport(key, label, res["company_id"])
        return

    # Mindbody BW widget (le33foch-style) → scrape HTTP
    if platform == "mindbody" and res.get("widget_id") and not res.get("needs_playwright"):
        print(f"→ {key:30s} mindbody BW widget={res['widget_id']}")
        scrape_mindbody_http(key, label, res["widget_id"])
        return

    # Mindbody healcode → engine dédié (widget <healcode-widget data-type="schedules">)
    # On y envoie aussi les mindbody « needs_playwright » : la discovery du
    # discover ne cherchait que le widget BW, pas la balise <healcode-widget>,
    # et une partie de ces marques a en réalité un planning healcode lisible en
    # HTTP (ex. Bikram Yoga Paris). Si l'engine ne trouve rien, on retombe sur
    # le message needs_playwright plus bas — aucune marque n'est perdue.
    if platform in ("mindbody_healcode", "mindbody"):
        site = res.get("site_id") or res.get("mb_site_id")
        print(f"→ {key:30s} mindbody healcode site={site or '?'}")
        if scrape_mindbody_healcode(key, label, res):
            return

    # Resamania (Resamania II / Stadline) → engine dédié
    if platform == "resamania":
        print(f"→ {key:30s} resamania slug={res.get('slug') or key}")
        scrape_resamania(key, label, res)
        return

    # Sportigo → engine dédié. On route aussi sur le registre de l'engine :
    # une marque que la discovery n'a pas su classer (elle sonde le site
    # vitrine, pas le sous-domaine .sportigo.club) reste scrapable.
    if platform == "sportigo" or key in _engine_brand_keys("engine_sportigo"):
        print(f"→ {key:30s} sportigo slug={res.get('slug') or key}")
        scrape_sportigo(key, label, res)
        return

    # ClubReady (en fait Glofox — cf. engine_clubready.py) → engine dédié.
    # Même logique de repli sur le registre : les Club Pilates parisiens
    # pointent tous sur la homepage clubpilates.fr, où aucune signature n'est
    # détectable ; leur branchId Glofox vit dans engine_clubready.SOURCES.
    if platform in ("clubready", "glofox") or key in _engine_brand_keys("engine_clubready"):
        print(f"→ {key:30s} clubready/glofox branch="
              f"{res.get('branch_id') or res.get('club_id') or 'registre'}")
        scrape_clubready(key, label, res)
        return

    # Le Five (backend propriétaire SPLF) → engine dédié.
    # Repli sur le registre de l'engine comme pour sportigo/clubready : la
    # discovery sonde lefive.fr/centre/<slug>/, qui renvoie 404 pour tous les
    # centres — aucune signature n'y est détectable, l'identifiant technique
    # (center_id) ne vit que dans le catalogue JSON du groupe.
    if platform == "lefive" or key in _engine_brand_keys("engine_lefive"):
        print(f"→ {key:30s} lefive center={res.get('center_id') or 'registre'}")
        scrape_lefive(key, label, res)
        return

    # UrbanSoccer (myurban.fr) → engine dédié, même repli.
    if platform == "urbansoccer" or key in _engine_brand_keys("engine_urbansoccer"):
        print(f"→ {key:30s} urbansoccer center={res.get('center_id') or 'registre'}")
        scrape_urbansoccer(key, label, res)
        return

    # Extraclub (Hoops Factory) : plateforme IDENTIFIÉE mais verrouillée.
    # /api/reservation/new/search exige un person_id de compte client ET un
    # header X-WSSE dérivé d'identifiants de service. Sans compte fourni par le
    # client, aucun créneau n'est accessible — on le signale à chaque run
    # plutôt que de le taire, pour que le blocage reste visible.
    if platform == "extraclub":
        print(f"⏳ {key:30s} extraclub — bloqué : "
              f"{res.get('blocker', 'compte client requis')} "
              f"(détail dans la note du fichier resolved)")
        return

    # Arketa, etc. → TODO
    if platform == "arketa":
        print(f"⏳ {key:30s} {platform} — engine à coder (skipped)")
        return

    # needs_playwright (Corpoz, Elevate, SPA) → reformation_evocore pattern
    if res.get("needs_playwright"):
        print(f"⏳ {key:30s} needs_playwright — déléguer à pattern reformation_evocore (TODO)")
        return

    # Retry au prochain discover
    if status == "retry":
        print(f"↻ {key:30s} {res.get('note', 'retry')}")
        return

    print(f"⊘ {key:30s} platform={platform or 'unknown'} status={status} — skip")


def run_scrape(brands_cfg, resolved_path, label="EXT"):
    """Scrape toutes les marques résolues d'un catalogue. Retourne (ok, erreurs)."""
    brands = load_or_die(brands_cfg)
    if not os.path.exists(resolved_path):
        print(f"⚠️ [{label}] {resolved_path} introuvable — lance la discovery d'abord.",
              file=sys.stderr)
        return 0, 0
    resolved = json.load(open(resolved_path, encoding="utf-8"))

    ok = err = 0
    for key, b in brands.items():
        if key.startswith("_"):
            continue
        try:
            scrape_brand(key, b, resolved)
            ok += 1
        except Exception as e:  # noqa: BLE001
            err += 1
            print(f"❌ {key} : {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    print(f"[{label}] {ok} marques traitées, {err} erreurs")
    return ok, err


def main():
    run_scrape(BRANDS_CFG, RESOLVED, "PILATES")


if __name__ == "__main__":
    main()
