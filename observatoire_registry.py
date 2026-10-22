"""Registre de l'Observatoire : marques, groupes, secteurs, sites et géographie.

POURQUOI
Les métadonnées des marques étaient éparpillées (supabase_sync.BRANDS,
comparateur.html, catalogues d'extensions, pilates_idf_compute…) et aucune
notion de GROUPE ni de VILLE n'existait : un studio n'était qu'un libellé
`lieu` brut (« STUDIO REFORMER - Neuilly », « Salle 1 Bastille »…). Ce module
est la source unique dont se sert observatoire_build.py.

TROIS NIVEAUX
  groupe  → Monday Group, Sanctuary Group, The New Me… (une marque seule est
            son propre groupe)
  marque  → une enseigne commerciale, éventuellement alimentée par plusieurs
            stores (Club Pilates = 4 stores) ; les stores en double (même
            company bsport scrapée sous plusieurs noms) sont listés une fois.
  site    → un emplacement physique d'une marque, déduit du `lieu` par le
            lexique géographique ci-dessous (« Episod · Pigalle »).
"""
import json
import os
import re
import unicodedata

# ---------------------------------------------------------------- groupes --
# Appartenances documentées dans le dépôt (discovery.json, brand_prices.json,
# prices_scrape.py). Aqua By : company bsport 2444, contiguë aux 2440-2443 du
# Sanctuary Group — rattachement présumé.
GROUPES = {
    "Monday Group": ["punch", "dynamo", "riise"],
    "Sanctuary Group": ["lecercle", "spacecycle", "poses", "driphiit",
                        "decibel", "aqua_by"],
}

# ----------------------------------------------------------------- marques --
# key: label, secteur, catégorie, stores (le premier porte la page détaillée),
#      cp par défaut (si le lieu ne dit rien), prix par défaut (€/visite).
# Secteurs : Fitness, Pilates, Yoga, Bien-être.
MARQUES = {
    # --- cœur fitness (scrapers dédiés) ---------------------------------
    "reset":       ("Re-SET", "Fitness", "Bootcamp", ["reset"], "75008", 25),
    "punch":       ("Punch", "Fitness", "Boxe", ["punch"], "75009", 18),
    "dynamo":      ("Dynamo", "Fitness", "Cycling", ["dynamo"], "75008", 22),
    "riise":       ("Riise", "Yoga", "Yoga / Wellness", ["riise"], "75008", 21),
    "thenewme":    ("The New Me", "Pilates", "Reformer", ["thenewme"], "75017", 28),
    "lecercle":    ("Le Cercle", "Fitness", "Boxe", ["lecercle"], "75010", 27),
    "spacecycle":  ("Space Cycle", "Fitness", "Cycling", ["spacecycle"], "75010", 28),
    "poses":       ("Poses", "Pilates", "Pilates / Barre", ["poses", "poses_studio_levallois"], "75003", 30),
    "barrys":      ("Barry's", "Fitness", "Bootcamp", ["barrys"], "75008", 30),
    "episod":      ("Episod", "Fitness", "Bootcamp", ["episod"], "75002", 21),
    "belly":       ("Belly", "Pilates", "Pilates", ["belly"], "75008", 30),
    "athletx":     ("AthletX", "Fitness", "Bootcamp", ["athletx"], "92200", 19),
    "senseclub":   ("Sense-Club", "Pilates", "Pilates", ["senseclub"], "75008", 45),
    "dna":         ("DNA Pilates", "Pilates", "Reformer", ["dna"], "75016", 38),
    "le33foch":    ("Le 33 Foch", "Fitness", "Bootcamp", ["le33foch"], "75016", 30),
    "banote":      ("Banote", "Pilates", "Lagree", ["banote"], "75016", 38),
    "santroch":    ("Sant-Roch", "Bien-être", "Sauna / Bains", ["santroch"], "75001", 40),
    "snakeandtwist": ("Snake & Twist", "Pilates", "Pilates", ["snakeandtwist"], "75011", 28),
    "burningbar":  ("Burning Bar", "Pilates", "Hot Pilates", ["burningbar"], "75016", 35),
    "driphiit":    ("DRIP HIIT", "Fitness", "HIIT", ["driphiit"], "75010", 29),
    "kore":        ("KORE Studio", "Pilates", "Pilates", ["kore"], "75017", 28),
    # --- groupes multi-stores / doublons fusionnés ----------------------
    "aqua_by":     ("Aqua By", "Fitness", "Aqua cycling", ["aqua_by_bastille"], "75011", 30),
    "decibel":     ("Decibel Studio", "Fitness", "Danse / Workout", ["decibel_studio_beaubourg"], "75004", 28),
    "club_pilates": ("Club Pilates", "Pilates", "Reformer", ["club_pilates_champ", "club_pilates_convention", "club_pilates_pompe", "club_pilates_saint_maur"], "75015", 30),
    "bikram":      ("Bikram Yoga Paris", "Yoga", "Hot yoga", ["bikram_yoga_paris"], "75004", 25),
    "the_space_yoga": ("The Space Yoga", "Yoga", "Yoga", ["the_space_yoga_belleville"], "75020", 22),
    "pilatestudio": ("Pilatestudio", "Pilates", "Pilates", ["pilatestudio_boulogne"], "92100", 28),
    "yuj":         ("YUJ Yoga", "Yoga", "Hot yoga", ["yuj_paris"], "75007", 25),
    "cercles_forme": ("Cercles de la Forme", "Fitness", "Salle de sport", ["cercles_forme"], "75011", 12),
}

# Stores jamais lus : doublons stricts d'un autre store (vérifié le 22/10 :
# 100 % de séances communes), pages vides, ou modèle « terrain » (padel, foot).
STORES_EXCLUS = {
    "aqua_by_boulogne", "aqua_by_charonne", "aqua_by_r_aumur",
    "the_space_yoga_nation", "studio_newformer_passy",   # ⊂ thenewme
    "bikram_marais", "bikram_grds_blvds", "yuj_yoga_wagram",
    "pilatestudio_paris_15", "flexlab", "pyra", "pyra_yoga", "anybuddy",
    "padel_idf", "padel_national", "padel_insights", "pilates_idf", "yoga_idf",
}
STORES_EXCLUS_RE = re.compile(r"^(le_five_|urbansoccer_|padel_)")

CATALOGUES = (
    ("pilates_extension_brands.json", "Pilates"),
    ("yoga_extension_brands.json", "Yoga"),
    ("fitness_extension_brands.json", "Fitness"),
)

PRIX_SECTEUR = {"Pilates": 30, "Yoga": 22, "Fitness": 24, "Bien-être": 35}

# Lieux qui ne sont pas des studios : séances en ligne, retraites à l'étranger.
EN_LIGNE_RE = re.compile(r"\b(en ligne|online|zoom|replay|live)\b", re.I)
HORS_SITE_RE = re.compile(r"(lodge|hotel|hôtel|riad|ouarzazate|retraite|moulin du|ibiza|marrakech|essaouira)", re.I)


def _n(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


# ------------------------------------------------------- lexique géo -------
# mot-clé (normalisé) -> (nom de site affiché, cp, ville). Le plus long mot-clé
# trouvé dans le lieu l'emporte (« place de clichy » avant « clichy »).
_GEO = [
    # Paris, par quartier
    ("louvre", "Louvre", "75001"), ("chatelet", "Châtelet", "75001"),
    ("saint honore", "Saint-Honoré", "75001"), ("st honore", "Saint-Honoré", "75001"),
    ("bourse", "Bourse", "75002"), ("sentier", "Sentier", "75002"),
    ("reaumur", "Réaumur", "75002"), ("bonne nouvelle", "Bonne Nouvelle", "75002"),
    ("marais", "Marais", "75003"), ("charlot", "Charlot", "75003"),
    ("temple", "Temple", "75003"), ("arts et metiers", "Arts et Métiers", "75003"),
    ("beaubourg", "Beaubourg", "75004"), ("tournelles", "Tournelles", "75004"),
    ("saint jacques", "Saint-Jacques", "75005"), ("st jacques", "Saint-Jacques", "75005"),
    ("saint germain des pres", "Saint-Germain", "75006"),
    ("vavin", "Vavin", "75006"), ("cherche midi", "Cherche-Midi", "75006"),
    ("invalides", "Invalides", "75007"), ("bosquet", "Bosquet", "75007"),
    ("champ de mars", "Champ de Mars", "75007"), ("sevres", "Sèvres", "75007"),
    ("madeleine", "Madeleine", "75008"), ("miromesnil", "Miromesnil", "75008"),
    ("boetie", "La Boétie", "75008"), ("saint lazare", "Saint-Lazare", "75008"),
    ("st lazare", "Saint-Lazare", "75008"), ("monceau", "Monceau", "75008"),
    ("tilsitt", "Étoile", "75008"), ("etoile", "Étoile", "75008"),
    ("opera", "Opéra", "75009"), ("lafayette", "Lafayette", "75009"),
    ("pigalle", "Pigalle", "75009"), ("maubeuge", "Maubeuge", "75009"),
    ("cadet", "Cadet", "75009"), ("douai", "Douai", "75009"),
    ("magenta", "Magenta", "75010"), ("grands boulevards", "Grands Boulevards", "75010"),
    ("colonel fabien", "Colonel Fabien", "75010"), ("vinaigriers", "Vinaigriers", "75010"),
    ("republique", "République", "75011"), ("oberkampf", "Oberkampf", "75011"),
    ("bastille", "Bastille", "75011"), ("charonne", "Charonne", "75011"),
    ("voltaire", "Voltaire", "75011"),
    ("nation", "Nation", "75012"), ("picpus", "Picpus", "75012"),
    ("tolbiac", "Tolbiac", "75013"), ("olympiades", "Olympiades", "75013"),
    ("raspail", "Raspail", "75014"),
    ("vaugirard", "Vaugirard", "75015"), ("convention", "Convention", "75015"),
    ("lecourbe", "Lecourbe", "75015"),
    ("passy", "Passy", "75016"), ("mozart", "Mozart", "75016"),
    ("trocadero", "Trocadéro", "75016"), ("victor hugo", "Victor Hugo", "75016"),
    ("yvon villarceau", "Victor Hugo", "75016"), ("dauphine", "Dauphine", "75016"),
    ("33 foch", "Foch", "75016"),
    ("wagram", "Wagram", "75017"), ("batignolles", "Batignolles", "75017"),
    ("villiers", "Villiers", "75017"), ("place de clichy", "Place de Clichy", "75017"),
    ("champerret", "Champerret", "75017"), ("maillot", "Maillot", "75017"),
    ("cardinet", "Cardinet", "75017"), ("joseph de maistre", "Joseph de Maistre", "75018"),
    ("collectionneur", "Collectionneur", "75017"),
    ("montmartre", "Montmartre", "75018"), ("ornano", "Ornano", "75018"),
    ("championnet", "Championnet", "75018"), ("simplon", "Simplon", "75018"),
    ("francoeur", "Francœur", "75018"),
    ("bolivar", "Bolivar", "75019"), ("belleville", "Belleville", "75020"),
    ("champs elysees", "Champs-Élysées", "75008"), ("bel air", "Bel-Air", "75012"),
    ("montparnasse", "Montparnasse", "75014"), ("alesia", "Alésia", "75014"),
    ("la villette", "La Villette", "75019"), ("chateau rouge", "Château Rouge", "75018"),
    ("maubert", "Maubert", "75005"), ("menilmontant", "Ménilmontant", "75020"),
    ("parmentier", "Parmentier", "75011"), ("goncourt", "Goncourt", "75011"),
    ("jourdain", "Jourdain", "75020"), ("gambetta", "Gambetta", "75020"),
    ("porte d italie", "Porte d'Italie", "75013"),
    # Petite couronne
    ("boulogne", "Boulogne", "92100"), ("neuilly", "Neuilly", "92200"),
    ("levallois", "Levallois", "92300"), ("la defense", "La Défense", "92800"),
    ("defense", "La Défense", "92800"), ("suresnes", "Suresnes", "92150"),
    ("chatillon", "Châtillon", "92320"), ("colombes", "Colombes", "92700"),
    ("rueil", "Rueil-Malmaison", "92500"), ("issy", "Issy", "92130"),
    ("saint mande", "Saint-Mandé", "94160"), ("charenton", "Charenton", "94220"),
    ("saint maur", "Saint-Maur", "94100"), ("vincennes", "Vincennes", "94300"),
    ("bagnolet", "Bagnolet", "93170"), ("saint ouen", "Saint-Ouen", "93400"),
    ("pantin", "Pantin", "93500"), ("montreuil", "Montreuil", "93100"),
    # Grande couronne
    ("versailles", "Versailles", "78000"),
    ("saint germain en laye", "Saint-Germain-en-Laye", "78100"),
    ("palaiseau", "Palaiseau", "91120"), ("enghien", "Enghien", "95880"),
    # Régions
    ("rennes", "Rennes", "35000"), ("rouen", "Rouen", "76000"),
    ("marseille", "Marseille", "13001"), ("nantes", "Nantes", "44000"),
    ("lille", "Lille", "59000"), ("reims", "Reims", "51100"),
    ("strasbourg", "Strasbourg", "67000"), ("toulouse", "Toulouse", "31000"),
    ("montpellier", "Montpellier", "34000"), ("angers", "Angers", "49000"),
    ("brest", "Brest", "29200"), ("lorient", "Lorient", "56100"),
    ("le havre", "Le Havre", "76600"), ("orleans", "Orléans", "45000"),
    ("annecy", "Annecy", "74000"), ("aix en provence", "Aix-en-Provence", "13100"),
    ("clermont ferrand", "Clermont-Ferrand", "63000"),
    ("st laurent du var", "Saint-Laurent-du-Var", "06700"),
    ("saint laurent du var", "Saint-Laurent-du-Var", "06700"),
    ("nice", "Nice", "06000"), ("pernes les fontaines", "Pernes-les-Fontaines", "84210"),
    ("bruxelles", "Bruxelles", "B1000"),
]
_GEO.sort(key=lambda g: -len(g[0]))

VILLES_CP = {
    "92100": "Boulogne-Billancourt", "92200": "Neuilly-sur-Seine",
    "92300": "Levallois-Perret", "92800": "Puteaux", "92150": "Suresnes",
    "92320": "Châtillon", "92700": "Colombes", "92500": "Rueil-Malmaison",
    "92130": "Issy-les-Moulineaux", "94160": "Saint-Mandé", "94220": "Charenton",
    "94100": "Saint-Maur", "94300": "Vincennes", "93170": "Bagnolet",
    "93400": "Saint-Ouen", "93500": "Pantin", "93100": "Montreuil",
    "78000": "Versailles", "78100": "Saint-Germain-en-Laye", "91120": "Palaiseau",
    "95880": "Enghien-les-Bains", "35000": "Rennes", "76000": "Rouen",
    "13001": "Marseille", "44000": "Nantes", "59000": "Lille", "51100": "Reims",
    "67000": "Strasbourg", "31000": "Toulouse", "34000": "Montpellier",
    "49000": "Angers", "29200": "Brest", "56100": "Lorient", "76600": "Le Havre",
    "45000": "Orléans", "74000": "Annecy", "13100": "Aix-en-Provence",
    "63000": "Clermont-Ferrand", "06700": "Saint-Laurent-du-Var", "06000": "Nice",
    "84210": "Pernes-les-Fontaines", "B1000": "Bruxelles",
}

_ARR_RE = re.compile(r"\b(?:paris\s*)?(\d{1,2})\s*(?:e|eme|er)\b|\bparis\s+(\d{1,2})\b")


def ville_de(cp):
    cp = str(cp or "")
    if cp.startswith("75") and len(cp) == 5:
        a = int(cp[3:])
        return "Paris", f"Paris {a}{'er' if a == 1 else 'e'}"
    v = VILLES_CP.get(cp)
    if v:
        return v, v
    return (f"CP {cp}" if cp else "Inconnu"), (f"CP {cp}" if cp else "Inconnu")


def region_de(cp):
    cp = str(cp or "")
    if cp.startswith("B"):
        return "Belgique"
    if cp.startswith("75"):
        return "Paris"
    if cp[:2] in ("92", "93", "94"):
        return "Petite couronne"
    if cp[:2] in ("77", "78", "91", "95"):
        return "Grande couronne"
    return "Régions" if cp else "Inconnu"


def geo_du_lieu(lieu):
    """(nom de site, cp) si le lieu contient un repère connu, sinon None."""
    t = " " + _n(lieu) + " "
    for cle, nom, cp in _GEO:
        if f" {cle} " in t:
            return nom, cp
    m = _ARR_RE.search(t)
    if m:
        a = int(m.group(1) or m.group(2))
        if 1 <= a <= 20:
            return f"Paris {a}{'er' if a == 1 else 'e'}", f"750{a:02d}"
    return None


def _charger(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _prix_marque(key, defaut, secteur, prix):
    """Prix moyen par visite : médiane des packs publiés, sinon défaut."""
    p = prix.get(key) or {}
    unit = sorted(x.get("prix_unitaire") for x in (p.get("packs") or [])
                  if x.get("prix_unitaire"))
    if unit:
        return round(unit[len(unit) // 2], 2)
    if p.get("drop_in"):
        return round(p["drop_in"] * 0.8, 2)
    return defaut or PRIX_SECTEUR.get(secteur, 25)


def marques():
    """Liste complète des marques, cœur + extensions découvertes, sans doublon."""
    prix = _charger("brand_prices.json")
    stores_pris = set(STORES_EXCLUS)
    groupe_de = {k: g for g, ks in GROUPES.items() for k in ks}
    out = {}
    for key, (label, secteur, cat, stores, cp, p) in MARQUES.items():
        stores = [s for s in stores if os.path.exists(f"{s}_data.json")]
        if not stores:
            continue
        stores_pris.update(stores)
        out[key] = {"key": key, "label": label, "secteur": secteur, "categorie": cat,
                    "stores": stores, "cp": cp, "groupe": groupe_de.get(key, label),
                    "prix": _prix_marque(key, p, secteur, prix),
                    "page": f"{stores[0]}.html"}
    for path, secteur in CATALOGUES:
        cat = _charger(path)
        for key, b in cat.items():
            if not isinstance(b, dict) or key in stores_pris or STORES_EXCLUS_RE.match(key):
                continue
            if not os.path.exists(f"{key}_data.json"):
                continue
            stores_pris.add(key)
            cps = b.get("cp") or [""]
            label = (b.get("label") or key).strip()
            out[key] = {"key": key, "label": label, "secteur": secteur,
                        "categorie": (b.get("type") or secteur).split("+")[0].strip(),
                        "stores": [key], "cp": cps[0], "groupe": groupe_de.get(key, label),
                        "prix": _prix_marque(key, None, secteur, prix),
                        "page": f"{key}.html"}
    return out


def site_du_lieu(marque, lieu):
    """(nom du site, cp, type) pour un lieu brut d'une marque.

    type : « studio », « en ligne » ou None (hors site : retraite, hôtel —
    ignoré). Un lieu sans repère géographique tombe sur le site par défaut de
    la marque (salles nommées « Hot Room », « Salle REF », « Arena »…).
    """
    l = str(lieu or "")
    if EN_LIGNE_RE.search(l):
        return "En ligne", "", "en ligne"
    if HORS_SITE_RE.search(l):
        return None, None, None
    g = geo_du_lieu(l)
    if g:
        return g[0], g[1], "studio"
    _, quartier = ville_de(marque["cp"])
    return quartier, marque["cp"], "studio"
