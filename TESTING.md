# Guide tests — pourquoi et comment

## Pourquoi
Aujourd'hui les scrapers cassent **en silence** quand une plateforme change son JSON. Le seul radar = `sanity_check.py` (qui détecte un volume effondré mais avec un délai d'1+ jour). Trois couches manquent :

1. **Smoke tests scrapers** — vérifier qu'un scraper retourne au moins 1 row valide avant de pousser
2. **Schema validation** — vérifier que les rows ont bien les fields attendus (`date`, `heure`, `presents`, etc.)
3. **Snapshot tests HTML** — vérifier que les pages générées n'ont pas de placeholder `__XYZ__` non remplacé

## Plan d'implémentation (par ordre d'impact)

### Phase 1 — Smoke tests scrapers (1h de boulot)
Crée `tests/test_smoke_scrapers.py` :
```python
import importlib
import pytest

SCRAPERS = ["banote", "dna", "le33foch", "bsport", "barrys", "santroch",
            "episod", "anybuddy", "snakeandtwist", "burningbar", "senseclub",
            "playtomic", "doinsport", "urbanpadel"]

@pytest.mark.parametrize("name", SCRAPERS)
def test_scraper_imports(name):
    """Le module Python charge sans crash (pas de syntax error / import manquant)."""
    importlib.import_module(f"{name}_scrape")

@pytest.mark.parametrize("name", SCRAPERS)
def test_scraper_has_main(name):
    m = importlib.import_module(f"{name}_scrape")
    assert hasattr(m, "main") or hasattr(m, "run"), f"{name} : pas de main()/run()"
```
Ajout workflow `.github/workflows/tests.yml` :
```yaml
on: [pull_request, push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: "pip" }
      - run: pip install pytest
      - run: pytest tests/ -v
```

### Phase 2 — Schema validation (2h)
Pour chaque scraper qui produit `*_data.json`, valider après écriture :
```python
# tests/test_schemas.py
REQUIRED_FIELDS = {
    "banote": {"date", "heure", "lieu", "cours", "statut", "presents", "capacite", "finie", "releve"},
    "bsport": {"date", "heure", "lieu", "cours", "presents", "capacite", "finie", "releve"},
    "anybuddy": {"date", "heure", "terrain", "statut", "duree", "prix"},
    # etc.
}

@pytest.mark.parametrize("brand,fields", REQUIRED_FIELDS.items())
def test_store_schema(brand, fields):
    path = f"{brand}_data.json"
    if not os.path.exists(path):
        pytest.skip(f"{path} absent")
    d = json.load(open(path))
    for k, row in list(d.items())[:50]:    # sample 50 rows
        assert isinstance(row, dict), f"{brand}/{k} pas un dict"
        missing = fields - row.keys()
        assert not missing, f"{brand}/{k} manque : {missing}"
```

### Phase 3 — Snapshot HTML (1h)
Vérifier qu'aucun placeholder `__XYZ__` ne traîne dans les HTML générés :
```python
# tests/test_html_no_placeholders.py
import glob
import re
import pytest

@pytest.mark.parametrize("path", glob.glob("*.html"))
def test_no_placeholders(path):
    s = open(path).read()
    placeholders = re.findall(r"__[A-Z][A-Z0-9_]+__", s)
    assert not placeholders, f"{path} : placeholders non remplacés : {placeholders[:5]}"
```

### Phase 4 — Intégration plateforme (live, 3h)
Pour chaque scraper, un test live optionnel (skippé en CI rapide, run via `--live`) :
```python
@pytest.mark.live
def test_banote_fetches_at_least_one_session():
    rows = banote_fetch.fetch_all()
    assert rows, "Banote : 0 séances retournées — plateforme down ou contrat cassé ?"
```
Lancé via workflow nocturne `.github/workflows/tests-live.yml` (4h matin Paris).

## Quick win immédiat (15 min)
Si pas le temps de tout faire : juste **Phase 1** suffit à attraper 80% des régressions (imports cassés, syntax errors). Ajoute aussi à chaque scraper en fin de `main()` :
```python
if not rows: sys.exit("ERREUR : 0 séances, scraper probablement cassé.")
```
→ le workflow CI tournera rouge tout seul sans framework de tests.
