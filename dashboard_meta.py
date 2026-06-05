"""Méta-données par scraper : méthode, risques/limites, fréquence.

Utilisé par chaque write_html() pour injecter le panneau META dans le
dashboard via template_common.meta_panel_html().
"""

META = {
    "reset": {
        "method": "API bsport directe (presents/capacite exacts)",
        "risk": "Aucune limite majeure. Données complètes pour les séances passées.",
        "freq": "Quotidien (4h Paris)",
    },
    "banote": {
        "method": "Widget Mindbody healcode (HTTP)",
        "risk": "Pas de compte exact (statut uniquement). CAP=12 estimée. Throttle Mindbody possible depuis IP CI.",
        "freq": "Toutes les 30 min (4h-21h Paris)",
    },
    "dna": {
        "method": "Widget Mindbody healcode (HTTP, UA Mac Safari)",
        "risk": "Pas de compte exact (statut uniquement). CAP=12 estimée. Throttle Mindbody possible.",
        "freq": "Toutes les 30 min (workflow isolé, offset +5 min)",
    },
    "le33foch": {
        "method": "Widget Mindbody (HTTP + fallback Playwright si throttle)",
        "risk": "Pas de compte exact. CAP=12 estimée. Fallback navigateur headless quand HTTP bloqué.",
        "freq": "Toutes les 30 min (workflow isolé, offset +10 min)",
    },
    "burningbar": {
        "method": "Widget Mindbody (HTTP) — The Hot Room + The Reformer Room",
        "risk": "Statut + 'places restantes' (X places) → présents dérivés (CAP=25). Pas exact.",
        "freq": "Toutes les 10 min",
    },
    "senseclub": {
        "method": "Widget Mindbody (HTTP)",
        "risk": "Cours 5 places max. 'Presque complet — reste X places' → présents = 5−X (estimation correcte).",
        "freq": "Toutes les 30 min",
    },
    "barrys": {
        "method": "API Mariana Tek directe (presents/capacite exacts)",
        "risk": "Données exactes pour séances terminées. Pas de limitation majeure.",
        "freq": "Toutes les 30 min (live-status)",
    },
    "santroch": {
        "method": "API Mariana Tek directe (presents/capacite exacts)",
        "risk": "Données exactes pour séances terminées.",
        "freq": "Toutes les 30 min (live-status)",
    },
    "episod": {
        "method": "API resamania (plan de salle)",
        "risk": "Plan de salle parfois absent → 0 attendance pour cette séance. Sinon exact.",
        "freq": "Toutes les 30 min (live-status)",
    },
    "anybuddy": {
        "method": "Robot disparition de créneaux (occupation reconstruite)",
        "risk": "Un créneau qui disparaît avant son heure = réservé. Pas le nom des joueurs.",
        "freq": "Toutes les 30 min (live-status)",
    },
    "snakeandtwist": {
        "method": "API Arketa directe (max_capacity / total_booked exacts)",
        "risk": "Données exactes. Fenêtre courante ~5 semaines.",
        "freq": "Toutes les 30 min (live-status)",
    },
    # bsport multi-marques (utilisé via bsport_scrape.run + studio_scrape.run)
    "bsport_generic": {
        "method": "API bsport directe (presents/capacite exacts)",
        "risk": "Données exactes pour séances passées. Pas de limitation majeure.",
        "freq": "2× / jour (~11h et 21h Paris)",
    },
}


def get(brand):
    """Retourne le dict META pour une marque, fallback générique sinon."""
    return META.get(brand) or META["bsport_generic"]
