// Fonctions communes à toutes les pages (connexion Supabase + appels à l'API DarkCodex).
let clientPromise = null;

function siteOrigin() {
  return (window.location && window.location.origin) ? window.location.origin : "";
}

function erreurApiMessage(erreur) {
  if (!erreur) return "Erreur inconnue.";
  if (typeof erreur === "string") return erreur;
  if (erreur.message) return erreur.message;
  if (erreur.error && erreur.error.message) return erreur.error.message;
  return String(erreur);
}

export async function config() {
  const reponse = await fetch("/config");
  if (!reponse.ok) {
    throw new Error("Configuration du site indisponible. Vérifiez SUPABASE_URL et SUPABASE_ANON_KEY sur Render.");
  }
  const donnees = await reponse.json();
  if (!donnees || !donnees.supabase_url || !donnees.supabase_anon_key) {
    throw new Error("Réponse /config incomplète : clé Supabase publique absente.");
  }
  return donnees;
}

export function getSupabase() {
  if (!clientPromise) {
    clientPromise = (async () => {
      const [{ createClient }, cfg] = await Promise.all([
        import("https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm"),
        config(),
      ]);
      return createClient(cfg.supabase_url, cfg.supabase_anon_key, {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: true,
        },
      });
    })();
  }
  return clientPromise;
}

export async function getSession() {
  const supabase = await getSupabase();
  const { data, error } = await supabase.auth.getSession();
  if (error) throw error;
  return data.session;
}

// Chemin de retour après connexion : uniquement un chemin du site (jamais un lien externe).
export function retourSur(chemin) {
  return typeof chemin === "string" && /^\/(?!\/)[A-Za-z0-9\-._~\/?=&%#]*$/.test(chemin) ? chemin : "/compte";
}

export function memoriserRetour(chemin) { localStorage.setItem("dk_retour", retourSur(chemin)); }
export function lireRetour() { return retourSur(localStorage.getItem("dk_retour") || "/compte"); }
export function oublierRetour() { localStorage.removeItem("dk_retour"); }

function redirectionConnexion(retour) {
  const url = new URL("/connexion", siteOrigin() || window.location.href);
  if (retour) url.searchParams.set("retour", retourSur(retour));
  return url.toString();
}

export async function connexionMotDePasse(email, motDePasse, retour = "/compte") {
  const supabase = await getSupabase();
  const { data, error } = await supabase.auth.signInWithPassword({ email, password: motDePasse });
  if (error) throw error;
  if (data && data.session) {
    const cible = redirectionConnexion(retour);
    if (window.location && window.location.href !== cible) {
      window.location.assign(cible);
    }
  }
}

export async function inscription(email, motDePasse, retour = "/compte") {
  const supabase = await getSupabase();
  const redirectTo = redirectionConnexion(retour);
  const { data, error } = await supabase.auth.signUp({
    email,
    password: motDePasse,
    options: { emailRedirectTo: redirectTo },
  });
  if (error) throw error;
  return { confirmationEmail: !data.session };
}

export async function connexionGoogle(retour = "/compte") {
  const supabase = await getSupabase();
  const redirectTo = redirectionConnexion(retour);
  const { error } = await supabase.auth.signInWithOAuth({
    provider: "google",
    options: { redirectTo },
  });
  if (error) throw error;
}

export async function lienMagique(email, retour = "/compte") {
  const supabase = await getSupabase();
  const redirectTo = redirectionConnexion(retour);
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: redirectTo },
  });
  if (error) throw error;
}

export async function motDePasseOublie(email, retour = "/compte") {
  const supabase = await getSupabase();
  const redirectTo = redirectionConnexion(retour);
  const { error } = await supabase.auth.resetPasswordForEmail(email, { redirectTo });
  if (error) throw error;
}

export async function deconnexion() {
  const supabase = await getSupabase();
  await supabase.auth.signOut();
}

// Appel à l'API DarkCodex avec le jeton de la session en cours.
export async function apiFetch(chemin, options = {}) {
  let session = null;
  try {
    session = await getSession();
  } catch (erreur) {
    session = null;
  }
  if (!session) throw new Error("Vous n'êtes pas connecté.");

  const headers = { ...(options.headers || {}), Authorization: "Bearer " + session.access_token };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const reponse = await fetch(chemin, { ...options, headers });
  if (reponse.status === 401) {
    throw new Error("Session expirée. Veuillez vous reconnecter.");
  }
  return reponse;
}

// À appeler en haut d'une page privée : renvoie la session, ou redirige vers /connexion.
export async function exigerConnexion() {
  let session = null;
  try {
    session = await getSession();
  } catch (erreur) {
    session = null;
  }
  if (!session) {
    memoriserRetour(location.pathname + location.search);
    location.replace("/connexion?retour=" + encodeURIComponent(location.pathname + location.search));
    return null;
  }
  return session;
}

export { erreurApiMessage };
