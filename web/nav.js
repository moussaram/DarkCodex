// En-tête, pied de page et bouton « Copier » partagés par toutes les pages du site.
(function () {
  var liens = [
    ["/", "Accueil"],
    ["/installation", "Installation"],
    ["/utilisation", "Utilisation"],
    ["/abonnements", "Tarifs"],
    ["/faq", "FAQ"],
  ];
  var chemin = location.pathname.replace(/\/+$/, "") || "/";

  function el(balise, classe, html) {
    var e = document.createElement(balise);
    if (classe) e.className = classe;
    if (html !== undefined) e.innerHTML = html;
    return e;
  }

  function construire() {
    var entete = el("header", "entete");
    var dedans = el("div", "entete-in");
    dedans.appendChild(el("a", "logo", "Dark<b>Codex</b>")).href = "/";

    var burger = el("button", "burger", "☰");
    burger.type = "button";
    burger.setAttribute("aria-label", "Menu");
    var menu = el("nav", "menu");
    liens.forEach(function (l) {
      var a = el("a", l[0] === chemin ? "actif" : "", l[1]);
      a.href = l[0];
      menu.appendChild(a);
    });
    var compte = el("a", chemin === "/compte" ? "actif" : "", "Mon compte");
    compte.href = "/compte";
    var connexion = el("a", "cta", "Se connecter");
    connexion.href = "/connexion";
    menu.appendChild(compte);
    menu.appendChild(connexion);
    burger.addEventListener("click", function () { menu.classList.toggle("ouvert"); });

    dedans.appendChild(burger);
    dedans.appendChild(menu);
    entete.appendChild(dedans);
    document.body.insertBefore(entete, document.body.firstChild);

    var pied = el("footer", "pied");
    pied.innerHTML =
      '<div class="pied-in">' +
      '<div><h4>DarkCodex</h4><span>Assistant IA de codage dans votre terminal.</span></div>' +
      '<div><h4>Découvrir</h4><a href="/">Présentation</a><a href="/abonnements">Tarifs</a><a href="/faq">Questions fréquentes</a></div>' +
      '<div><h4>Guides</h4><a href="/installation">Installation</a><a href="/utilisation">Utilisation</a><a href="/device">Lier un terminal</a></div>' +
      '<div><h4>Compte</h4><a href="/connexion">Connexion</a><a href="/compte">Mon compte</a><a href="/statut">État du service</a></div>' +
      '<div class="bas">© ' + new Date().getFullYear() + ' DarkCodex · <a style="display:inline" href="/legal">Conditions et confidentialité</a></div>' +
      "</div>";
    document.body.appendChild(pied);
  }

  function boutonsCopier() {
    document.querySelectorAll("pre").forEach(function (pre) {
      if (pre.querySelector(".copier")) return;
      var b = el("button", "copier secondaire", "Copier");
      b.type = "button";
      b.addEventListener("click", function () {
        var texte = pre.innerText.replace(/Copier\s*$/, "").trim();
        var fini = function () { b.textContent = "Copié ✓"; setTimeout(function () { b.textContent = "Copier"; }, 1800); };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(texte).then(fini, fini);
        else fini();
      });
      pre.appendChild(b);
    });
  }

  function demarrer() { construire(); boutonsCopier(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", demarrer);
  else demarrer();
})();
