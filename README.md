# Plateforme d'analyse de fréquentation — studios de sport Paris

Captation, normalisation et restitution de la fréquentation réelle des studios boutique parisiens (pilates, boxe, cycling, yoga, wellness, padel, bootcamp). Données mises à jour automatiquement via GitHub Actions, dashboards servis sur GitHub Pages.

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│           GitHub Actions (workflows cron)                            │
│   bsport 2x/j · live-status 30 min · live-senseclub 30 min           │
│   daily Re-SET 6h · studios Mindbody soir · sanity 6h · backup 1x/j  │
└────────────────────────┬─────────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│           Scrapers Python (urllib · Playwright)                      │
│                                                                      │
│ bsport_scrape   ◀── thenewme/lecercle/spacecycle/poses/belly/        │
│  (moteur)            athletx/snakeandtwist*/barrys/santroch* wrappers│
│ studio_scrape   ◀── punch/dynamo/riise wrappers                      │
│ reset_scrape    ◀── Re-SET (bsport, historique complet)              │
│ episod_scrape   ◀── Episod (resamania, plan de salle SVG)            │
│ anybuddy_scrape ◀── Trinquet (padel, occupation par disparition)     │
│ senseclub/burningbar/banote/dna/le33foch_scrape ◀── Mindbody widget  │
│                                                                      │
│   * snakeandtwist = Arketa, santroch/barrys = Mariana Tek            │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ safestore (atomic + anti-shrink)
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│           Stockage : git lui-même                                    │
│   <key>_data.json (dict {id: {...}}) — historique immuable           │
│   <key>.html (dashboard auto-rendu) + <key>_seances.csv              │
└────────────────────────┬─────────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│           GitHub Pages (sbbb-git.github.io/reset/)                   │
│   all-1cda8fabdb.html (hub à onglets) · comparateur.html · etude.html│
│   + chaque <key>.html dédié                                          │
└──────────────────────────────────────────────────────────────────────┘
```

## 📦 Plateformes maîtrisées

| Plateforme | Détection | API | Précision | Studios couverts |
|---|---|---|---|---|
| **bsport** | `cdn.bsport.io` + `companyId:<N>` | `/book/v1/offer/?company=<N>` | chiffres exacts | Re-SET, TNM ×27 companies, Le Cercle, Space Cycle, Poses, Belly ×5, AthletX ×2, Snake & Twist |
| **Mindbody plugin** (proxy WP) | `<host>/wp-json/mindbody/v1/class` | idem | chiffres exacts | Punch, Dynamo, Riise |
| **Mindbody widget** | `widgets.mindbodyonline.com/widgets/schedules/<id>/load_markup` | idem | statut seulement (+ capacité supposée) | Sense-Club, Burning Bar, Banote, DNA, Le 33 Foch |
| **Mariana Tek** | `<tenant>.marianatek.com/api/customer/v1/classes` | idem | chiffres exacts | Barry's, Sant-Roch |
| **resamania** | plan de salle SVG `/reservation/<id>/` | idem + planning HTML | sièges occupés | Episod |
| **Arketa** | `poweredby_arketa` + `app.arketa.co/iframe/<slug>/schedule` | `/api/widget/data?widgetName=<slug>&type=classes&start_time=<UNIX>` | chiffres exacts | Snake & Twist |
| **Anybuddy** | `anybuddyapp.com/api/v1/availabilities` | idem | occupation par disparition | Trinquet (padel) |

## 📐 Schéma de données commun

Chaque `<key>_data.json` est un dict `{id_session: {...}}` où chaque entrée respecte :

```json
{
  "id": "string",
  "date": "YYYY-MM-DD",
  "jour": "Lundi|...",
  "heure": "HH:MM",
  "fin": "HH:MM",
  "lieu": "Nom du studio",
  "cours": "Nom du cours",
  "coach": "Nom de l'instructeur",
  "capacite": 12,
  "presents": 8,
  "finie": true,
  "releve": "YYYY-MM-DD HH:MM"
}
```

Le **comparateur** (`comparateur.html`) consomme tous ces fichiers via fetch côté navigateur. Pour qu'une nouvelle marque apparaisse dans le comparateur, son `_data.json` doit suivre **strictement** ce schéma.

## ➕ Ajouter une nouvelle marque

1. Identifier la plateforme (curl la page de réservation, chercher les marqueurs ci-dessus).
2. Créer un wrapper :
   - **bsport** → `cp belly_scrape.py X_scrape.py` puis adapter `key, brand, company(ies), host, accent`.
   - **Mindbody plugin** → `cp punch_scrape.py X_scrape.py` puis adapter `host, api, accent`.
   - **Autre** → s'inspirer de `barrys_scrape.py` (Mariana), `episod_scrape.py` (resamania), `senseclub_scrape.py` (Mindbody widget) ou `snakeandtwist_scrape.py` (Arketa).
3. Lancer `python3 X_scrape.py` localement et vérifier `X_data.json` + `X.html`.
4. Ajouter au bon workflow (`.github/workflows/...`) : step `run: python3 X_scrape.py` + `git add X_data.json X_seances.csv X.html`.
5. Ajouter une entrée à `BRANDS` dans `comparateur.html` (clé/label/couleur/catégorie/plateforme).
6. Ajouter un onglet dans `all-1cda8fabdb.html` (`STUDIOS` array).

## 🔒 Durabilité

- **safestore.py** : écriture atomique (`os.replace`) + abandon si fichier illisible + garde anti-rétrécissement (refus d'écrire moins d'entrées qu'avant). Utilisé par tous les scrapers.
- **Git history** = archive immuable. Tout `_data.json` committé est récupérable par `git show <SHA>:<fichier>`.
- **sanity_check.py** (`workflow sanity` toutes les 6 h) : détecte overbooks, capacités aberrantes, delays, drops de volume — fait échouer le workflow → alerte healthcheck.
- **healthcheck** (optionnel) : si `HEALTHCHECK_URL` est défini en secret GitHub, chaque workflow pingue cet URL en fin de run → alerte mail via healthchecks.io si silence.
- **Backup Google Sheets** (optionnel) : si `SHEET_WEBHOOK_URL` défini, sauvegarde quotidienne complète vers un Google Sheet (script Apps Script `sheets_appscript.gs` à coller dans le Sheet).

## 🗂️ Fichiers clés

```
bsport_scrape.py     # moteur générique bsport
studio_scrape.py     # moteur Mindbody plugin WP
*_scrape.py          # wrappers ou scrapers autonomes
safestore.py         # stockage atomique anti-perte
sanity_check.py      # détection d'anomalies

comparateur.html     # hub d'analyse inter-marques
all-*.html           # hub à onglets (lien principal)
etude.html           # landing B2B "étude de marché"
test.html            # candidats discovery à valider
<key>.html           # dashboard par marque

<key>_data.json      # store par marque (clé = id session)
<key>_seances.csv    # export plat
discovery.json       # candidats trouvés par les agents

.github/workflows/   # cron jobs
CHECKLIST.md         # roadmap technique & business
```

## 🔄 Workflows

| Fichier | Fréquence | Rôle |
|---|---|---|
| `daily-scrape.yml` | matin 6h | Re-SET (historique complet depuis 22/03/2026) |
| `studios.yml` | soir 23h | Punch / Dynamo / Riise (Mindbody plugin) |
| `bsport-studios.yml` | 13h + 23h | TNM, Le Cercle, Space Cycle, Poses, Belly, AthletX, Snake & Twist |
| `live-status.yml` | 30 min (06h-23h Paris) | Barry's, Episod, Anybuddy, Banote, DNA, Le 33 Foch, Sant-Roch |
| `live-senseclub.yml` | 30 min (06h-23h Paris) | Sense-Club, Burning Bar (Playwright + Chromium) |
| `sanity.yml` | 6h | Anomalies dans les stores |
| `sheets-backup.yml` | 22h UTC | Sauvegarde Google Sheets (si configuré) |

## 🧭 Notes utiles

- `studios-f62fba8f66.html` et `hub3-75c69ff7b9.html` ont été retirés (le hub `all-*` les remplace).
- L'historique git est gardé minimaliste en ne committant que les `_data.json` qui changent (le workflow saute si `git diff --cached --quiet`).
- Le décompte « stats » sur chaque dashboard s'arrête à la 1ʳᵉ séance pas encore commencée (filtré côté JS).
- Pour les marques en statut-only (Mindbody widget) on suppose une capacité par salle ; seules les séances « complet » ou « presque complet » comptent dans le comparateur (les autres ont `capacite=0` par convention).

Voir `CHECKLIST.md` pour la roadmap.
