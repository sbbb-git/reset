// Test du parseur de burningbar_fetch.cjs — sans navigateur.
// Playwright n'est chargé qu'à l'exécution réelle (loadChromium), donc ce
// fichier s'exécute partout : node burningbar_fetch.test.cjs
const assert = require('assert');
const F = require('./burningbar_fetch.cjs');

const room = { salle: "Burning Bar Paris 16", id: "4d37769e50a" };
let ko = 0;
function check(nom, fn) {
  try { fn(); console.log(`  ok   ${nom}`); }
  catch (e) { ko++; console.log(`  KO   ${nom}\n       ${e.message}`); }
}

// 1. Format canonique observé en prod jusqu'au 2026-08-07 (texte aplati du
//    widget : heure, durée, cours, coach, bouton).
check("format FR canonique", () => {
  const txt = "Samedi 9 août 09:00 45 min Burning Bar Sophie Afficher les détails "
            + "Réserver 10:15 50 min Burning Sculpt Léa Il reste 3 places ! "
            + "11:30 45 min Burning Core Shape Marie Liste d'attente "
            + "12:30 45 min Burning Flow Julie Complet";
  const r = F.parseText(txt, room);
  assert.strictEqual(r.length, 4, `4 séances attendues, ${r.length} trouvées`);
  assert.deepStrictEqual(r[0], { salle: room.salle, heure: "09:00", duree: 45,
                                 cours: "Burning Bar Sophie", statut_brut: "Réserver" });
  assert.strictEqual(r[1].statut_brut, "Il reste 3 places !");
  assert.strictEqual(r[2].statut_brut, "Liste d'attente");
  assert.strictEqual(r[3].statut_brut, "Complet");
});

// 2. « Il ne reste qu'une place ! » -> normalize() côté Python en tire 1 place.
check("place unique", () => {
  const r = F.parseText("18:00 45 min Burning Sculpt Il ne reste qu'une place !", room);
  assert.strictEqual(r.length, 1);
  assert.match(r[0].statut_brut, /une place/);
});

// 3. Repli anglais : Mindbody sert le widget en EN si la locale n'est pas
//    reconnue. Avant, ce seul changement rendait 0 séance.
check("libellés anglais", () => {
  const txt = "09:00 45 min Burning Bar Book 10:15 50 min Burning Sculpt 3 spots left "
            + "11:30 45 min Burning Flow Join Waitlist";
  const r = F.parseText(txt, room);
  assert.strictEqual(r.length, 3, `3 séances attendues, ${r.length} trouvées`);
  assert.strictEqual(r[0].statut_brut, "Book");
});

// 4. Passe large : gabarit sans « N min ». duree = 0, le reste tient.
check("durée absente (passe large)", () => {
  const r = F.parseText("09:00 Burning Bar Réserver 10:15 Burning Sculpt Complet", room);
  assert.strictEqual(r.length, 2, `2 séances attendues, ${r.length} trouvées`);
  assert.strictEqual(r[0].duree, 0);
  assert.strictEqual(r[0].cours, "Burning Bar");
});

// 5. Page vide / studio fermé -> 0 séance, sans invention.
check("page vide", () => {
  assert.strictEqual(F.parseText("", room).length, 0);
  assert.strictEqual(
    F.parseText("Aucun cours programmé pour cette période.", room).length, 0);
});

// 6. Les deux widgets du site sont bien ceux publiés sur burningbar.fr
//    (l'ancien 4d43192e50a n'existe plus).
check("widgets à jour", () => {
  const ids = F.ROOMS.map(r => r.id);
  assert.deepStrictEqual(ids, ["4d37769e50a", "4d56911e50a"]);
  assert.ok(!ids.includes("4d43192e50a"), "l'id mort est encore là");
  assert.strictEqual(new Set(F.ROOMS.map(r => r.salle)).size, 2);
});

// 7. Un appel ne doit pas empoisonner le suivant (regex globales : lastIndex).
check("regex réutilisable", () => {
  const a = F.parseText("09:00 45 min Cours A Réserver", room);
  const b = F.parseText("09:00 45 min Cours A Réserver", room);
  assert.strictEqual(a.length, 1);
  assert.strictEqual(b.length, 1, "2e appel vide : lastIndex non remis à zéro");
});

console.log(ko ? `\n${ko} test(s) en échec` : "\ntous les tests passent");
process.exit(ko ? 1 : 0);
