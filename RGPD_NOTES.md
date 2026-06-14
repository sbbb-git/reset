# RGPD / Confidentialité — analyse et plan d'action (à activer si besoin)

Notes mises de côté pour quand on en aura besoin (lecture du post LinkedIn du 14/06/2026 sur la sanction CNIL 240k€ contre un scraper LinkedIn).

## Contexte du déclencheur
La CNIL a sanctionné une boîte qui scrapait LinkedIn pour faire de la prospection commerciale. Position officielle :
1. "Public" ≠ libre d'usage
2. Si la personne masque ses coords = opposition. Passer outre = faute
3. Il faut **informer** et **permettre l'opposition**, sinon non-conformité

Sources publiques **autorisées** à la réutilisation (avec conditions) : Sirene, France Travail, marchés publics.

## Évaluation pour le projet `reset`

| Aspect | Notre projet | Cas CNIL |
|---|---|---|
| Cible | Studios fitness, clubs padel (entités B2B) | Personnes physiques |
| Finalité | Benchmark concurrence, étude marché, pilotage Re-SET | Prospection commerciale directe |
| Contact | ❌ Aucun outreach | ✅ Outreach individuel |
| Données perso collectées | ⚠️ **Noms des coachs** (champ `r.coach`) | Coords pro complètes |
| Diffusion | Site password-protégé | Outil interne d'outreach |

**Verdict** : on n'est PAS dans le scénario CNIL frontal. Risque résiduel limité mais réel sur 2 points.

## Zones grises identifiées

### 🟡 Zone 1 — Noms de coachs
- Stocké dans tous les `*_data.json` (`banote_data.json`, `dna_data.json`, `barrys_data.json`, etc.)
- Stocké dans Supabase `sessions.coach`
- Analytics nominatif visible : "Coachs stars (moyenne de présents / cours)" dans tous les dashboards
- C'est de la donnée perso au sens RGPD même si visible sur le widget public Mindbody/bsport

### 🟡 Zone 2 — Pas de mention RGPD / information des personnes
- Pas de page "mentions légales" / "politique de confidentialité"
- Pas de mécanisme d'opposition documenté
- Les coachs ne sont pas informés de la collecte

## Mitigations possibles (ordre de priorité)

### 1. Anonymiser les coachs au stockage (30 min, clean 80% du risque)
```python
# Dans chaque scraper, au moment du store
import hashlib
COACH_SALT = "reset-2026-secret"  # rotation possible
def hash_coach(name):
    if not name: return ""
    return "C-" + hashlib.sha256((COACH_SALT + name.lower()).encode()).hexdigest()[:8]

store[key]["coach"] = hash_coach(s.get("coach"))
```
- "Coachs stars" devient "Coach C-A3F7E2" — identifiable de manière interne (anonyme à l'extérieur)
- Possible de garder une table de correspondance hash→nom dans un fichier privé / Supabase RLS auth
- Permet le droit d'accès indirect (un coach demande "qu'avez-vous sur moi" → on hash son nom, on retrouve la ligne)

### 2. Page `mentions-rgpd.html` (15 min)
À créer avec :
- Quelles données on collecte (séances, statuts, présents, prix — **pas de données client final**)
- Finalité : étude de marché, benchmark concurrence (intérêt légitime)
- Pas de prospection, pas de revente
- Sous-traitants : Supabase (hébergement BDD), GitHub (hébergement front)
- Droit d'opposition : email → sachabitoun17@gmail.com
- Durée de conservation : 2 ans glissants

### 3. Liste d'opposition (`coachs_opt_out.json`) (10 min)
```python
OPT_OUT = set(json.load(open("coachs_opt_out.json")))
def hash_coach(name):
    if name.lower() in OPT_OUT: return "[opt-out]"
    return "C-..." + hashlib.sha256(...).hexdigest()[:8]
```

### 4. Restreindre la rétention (déjà fait pour padel via `prune_padel_live.py`)
- Étendre au fitness boutique : pruner les séances > 6 mois si on n'a pas besoin de l'historique entier
- Ou au contraire : justifier la rétention longue par l'analyse d'évolution

### 5. DPA / déclaration CNIL si on commercialise
- Si on partage le tool / pitche à des investisseurs / le vend → faut un DPIA (Data Protection Impact Assessment)
- Pour usage interne / privé : déclaration simple suffisante

## Padel : zéro risque
La donnée padel est **purement opérationnelle** (taux d'occupation des terrains, prix, créneaux). Aucun nom de joueur, aucune coordonnée. RAS.

## Décision actuelle
"Pour l'instant je m'en tape" (user, 14/06/2026). On garde l'analyse au chaud pour activation si :
- Ouverture au-delà de l'usage perso (commercialisation, pitch invest, accès partagé)
- Demande explicite d'un coach / studio
- Évolution de la jurisprudence CNIL sur le B2B intelligence

## Quick-fix à 30 min si urgence
1. Hash des noms de coachs (mitigation 1)
2. Page `mentions-rgpd.html` (mitigation 2)
3. Commit + push

→ Couvre le scenario "quelqu'un te tape dessus, qu'est-ce que tu réponds dans la semaine".
