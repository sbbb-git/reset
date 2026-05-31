# Checklist — roadmap technique & business

Synthèse de la review du 30/05. Statut : `[ ]` à faire, `[x]` fait, `[~]` en cours, `[-]` plus tard.

## 🟢 Quick wins (à faire maintenant)
- [~] **1. Healthcheck** : ping healthchecks.io en fin de chaque workflow → alerte mail si silence > 6h (graceful si secret absent).
- [~] **2. Sanity checks** : script qui détecte les anomalies dans les `*_data.json` (présents>capacité, drop volume, capacité aberrante, last_update vieux) et fait échouer le workflow si seuil dépassé.
- [~] **3. README + schéma d'archi** : doc claire (plateformes, structure, comment ajouter un studio, workflows, schéma de données). Indispensable pour due dil.

## 🟡 Mid-effort (à reparler une fois données accumulées)
- [-] **4. Base de données managée** (Supabase Postgres free) : pour query SQL, exports, BI. **OK Supabase = suffisant** pour notre volume.
- [-] **5. Rapport mensuel auto** (PDF/Excel) envoyé le 1er du mois → le LIVRABLE qu'on vend.
- [-] **6. Indices de marché publics mensuels** (« Taux remplissage pilates Paris : 62 % ») → presse → leads.
- [-] **7. Alertes** : nouveau studio détecté / chute brutale / pic anomalie.
- [-] **8. Croisement données externes** : météo, jours fériés, vacances scolaires (impact ~20 % variance).

## 🔴 Gros chantiers (plus tard)
- [-] **9. Migration archi** : Cloudflare Pages + Access (auth) + Supabase. Sortir de GitHub Pages.
- [-] **10. API B2B** : `GET /api/v1/sessions?brand=...&from=...&to=...` avec clé API. Modèle pricing par requête/volume.
- [-] **11. Extension géographique** : Lyon, Marseille, Bordeaux → TAM ×3.
- [-] **12. Prévisions** : modèle saisonnalité + tendance à 2-4 semaines.

## 💼 Business / juridique
- [-] **13. Lire les CGU** des plateformes scrapées (bsport, Mindbody, Mariana, Arketa, resamania, Anybuddy) — certaines interdisent le scraping commercial.
- [-] **14. Anonymiser au niveau studio** dans les rapports publics (pas de noms de coachs nommément).
- [-] **15. Structure légale** (EI/SASU) avant de facturer + CGU + mention « données issues de sources publiques, à titre indicatif ».
- [-] **16. 3 RDV prospects** (1 opérateur, 1 investisseur, 1 équipementier) avant de figer les prix.
- [-] **17. Tarification témoignage** : 1-2 clients à 500 €/mois pour témoigner → puis monter.

## 📋 Plateformes restantes à explorer
- [-] **Sportigo** (Pyra, 11ème Round)
- [-] **Momence** (Kalon Barre)
- [-] Custom/Squarespace (Studio Rituel, OMM, Caudalie, Atelier de la Forme, Hundred Pilates, AURA…) — sans doute app native sans iframe, hors de portée.

## 📊 État actuel (30/05)
- 19 enseignes (chiffres exacts ×13 + statut estimé ×5 + occupation déduite ×1)
- 6 plateformes maîtrisées (bsport, Mindbody plugin, Mindbody widget, Mariana Tek, resamania, Arketa, Anybuddy)
- 5 workflows automatisés (bsport 2×/j, live-status 30 min, live-senseclub 30 min, daily 6h, studios soir)
- safestore anti-perte de données déployé sur tous les scrapers
- Sauvegarde Google Sheets via Apps Script préparée (manque la config des secrets)
- Pages publiques actuelles : `all-1cda8fabdb.html` (hub principal), `comparateur.html`, `test.html`, `etude.html`
