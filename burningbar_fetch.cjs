// Récupère le planning Mindbody de Burning Bar (2 adresses) via le widget rendu.
// Sortie: JSON sur stdout -> [{salle, heure, duree, cours, statut_brut}]
//
// MISE À JOUR 2026-08-09 — le studio est passé de 2 SALLES à 2 ADRESSES.
// burningbar.fr/planning/ n'expose plus « The Hot Room » / « The Reformer
// Room » mais deux widgets Branded Web V2, sous les ancres correspondantes :
//     <div class="mindbody-widget" data-widget-type="Schedules"
//          data-widget-id="4d37769e50a">   -> #planning-paris-16
//     <div class="mindbody-widget" data-widget-type="Schedules"
//          data-widget-id="4d56911e50a">   -> #planning-paris-7
// L'id 4d43192e50a (ex-Reformer Room) a disparu du site : le widget rendait
// donc une page vide, moitié du planning perdue. Vérifié le 2026-08-09 : la
// charge RSC de chaque widget déclare son lieu, « Burning Bar 16eme » pour
// 4d37769e50a et « Burning Bar 7eme » pour 4d56911e50a — d'où le nommage
// ci-dessous. Le 2e site Mindbody (data-site-id 128674 / mb-site-id 5747442)
// coexiste avec l'historique 116060 / 5734191.
function loadChromium() {
  try { return require('playwright').chromium; }
  catch (e) { return require('/opt/node22/lib/node_modules/playwright').chromium; }
}

const ROOMS = [
  { salle: "Burning Bar Paris 16", id: "4d37769e50a" },
  { salle: "Burning Bar Paris 7",  id: "4d56911e50a" },
];

// Libellés de bouton acceptés. Le widget est servi en fr-FR, mais Mindbody
// bascule en anglais quand la locale n'est pas reconnue : on accepte les deux
// plutôt que de rendre 0 séance sur un simple changement de langue.
const STATUT = String.raw`Réserver|Liste d'attente|Complet|Il ne reste qu'une place\s*!?|Il reste\s+\d+\s+places?\s*!?|Book|Join Waitlist|Waitlist|Full|Sold Out|Only\s+\d+\s+spots?\s+left\s*!?|\d+\s+spots?\s+left\s*!?`;
// Passe 1 : format canonique observé en prod jusqu'au 2026-08-07.
const RE_STRICT = new RegExp(String.raw`(\d{1,2}:\d{2})\s+(\d+)\s*min\s+(.+?)\s+(${STATUT})`, 'gi');
// Passe 2 : même ancrage (heure … bouton) mais durée facultative — utilisée
// seulement si la passe 1 ne trouve rien, pour survivre à un gabarit qui
// n'afficherait plus « N min ». Reste ancrée sur 2 repères forts, donc pas de
// risque d'aspirer du texte au hasard.
const RE_LARGE = new RegExp(String.raw`(\d{1,2}:\d{2})\s+(?:(\d+)\s*min\s+)?(.{3,80}?)\s+(${STATUT})`, 'gi');

function extract(txt, room, re) {
  const out = [];
  let m;
  re.lastIndex = 0;
  while ((m = re.exec(txt))) {
    const cours = m[3].split(/Afficher les détails|View details/i)[0]
                      .replace(/\s+/g, ' ').trim();
    if (!cours) continue;
    out.push({ salle: room.salle, heure: m[1], duree: +(m[2] || 0), cours,
               statut_brut: m[4].replace(/\s+/g, ' ').trim() });
  }
  return out;
}

// Texte aplati d'un widget -> séances. Isolée de Playwright pour être
// testable sans navigateur (burningbar_fetch.test.cjs).
function parseText(raw, room) {
  const txt = (raw || "").replace(/\s+/g, ' ');
  const strict = extract(txt, room, RE_STRICT);
  return strict.length ? strict : extract(txt, room, RE_LARGE);
}

async function main() {
  const chromium = loadChromium();
  const b = await chromium.launch();
  const out = [];
  for (const room of ROOMS) {
    const url = `https://go.mindbodyonline.com/book/widgets/schedules/view/${room.id}/schedule`;
    const ctx = await b.newContext({ ignoreHTTPSErrors: true, locale: 'fr-FR' });
    const p = await ctx.newPage();
    try {
      await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
      await p.waitForFunction(
        () => /(?:Réserver|Liste d'attente|Complet|place|Book|Waitlist|Full|spots)/i.test(document.body.innerText)
              && /\d{1,2}:\d{2}/.test(document.body.innerText),
        { timeout: 30000 }).catch(() => {});
      await p.waitForTimeout(2500);
      const raw = await p.evaluate(() => document.body.innerText);
      const txt = raw.replace(/\s+/g, ' ');
      const found = parseText(txt, room);
      out.push(...found);
      // Diagnostic sur stderr (stdout reste du JSON pur) : sans ça, un widget
      // qui rend une page vide et un widget dont le gabarit a changé donnent
      // le même « 0 séance » côté Python, indiscernables dans les logs.
      if (!found.length) {
        console.error(`[burningbar] ${room.salle} (${room.id}) : 0 séance — `
          + `${txt.length} car. rendus, échantillon : ${JSON.stringify(txt.slice(0, 300))}`);
      } else {
        console.error(`[burningbar] ${room.salle} (${room.id}) : ${found.length} séances`);
      }
    } catch (e) {
      console.error(`[burningbar] ${room.salle} (${room.id}) : échec rendu — ${e.message}`);
    }
    await ctx.close();
  }
  await b.close();
  process.stdout.write(JSON.stringify(out));
}

module.exports = { ROOMS, RE_STRICT, RE_LARGE, extract, parseText };

if (require.main === module) main();
