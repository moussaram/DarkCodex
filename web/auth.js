// Fonctions communes à toutes les pages (connexion Supabase + appels à l'API DarkCodex).
let clientPromise = null;

export async function config() {
  const reponse = await fetch("/config");
  if (!reponse.ok) throw new Error("Configuration du site indisponible.");
  return reponse.json();
}

export function getSupabase() {
  if (!clientPromise) {
    clientPromise = (async () => {
      const [{ createClient }, cfg] = await Promise.all([
        import("https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm"),
        config(),
      ]);
      return createClient(cfg.supabase_url, cfg.supabase_anon_key, {
        auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
      });
    })();
  }
  return clientPromise;
}

export async function getSession() {
  const supabase = await getSupabase();
  const { data } = await supabase.auth.getSession();
  return data.session;
}

// Chemin de retour après connexion : uniquement un chemin du site (jamais un lien externe).
export function retourSur(chemin) {
  return typeof chemin === "string" && /^\/(?!\/)[A-Za-z0-9\-._~\/?=&%#]*$/.test(chemin) ? chemin : "/compte";
}

export function memoriserRetour(chemin) { localStorage.setItem("dk_retour", retourSur(chemin)); }
export function lireRetour() { return retourSur(localStorage.getItem("dk_retour") || "/compte"); }
export function oublierRetour() { localStorage.removeItem("dk_retour"); }

export async function connexionMotDePasse(email, motDePasse) {
  const supabase = await getSupabase();
  const { error } = await supabase.auth.signInWithPassword({ email, password: motDePasse });
  if (error) throw error;
}

export async function inscription(email, motDePasse) {
  const supabase = await getSupabase();
  const { data, error } = await supabase.auth.signUp({
    email,
    password: motDePasse,
    options: { emailRedirectTo: location.origin + "/connexion" },
  });
  if (error) throw error;
  return { confirmationEmail: !data.session }; // true si Supabase exige de confirmer l'email
}

export async function connexionGoogle() {
  const supabase = await getSupabase();
  const { error } = await supabase.auth.signInWithOAuth({
    provider: "google",
    options: { redirectTo: location.origin + "/connexion" },
  });
  if (error) throw error;
}

export async function lienMagique(email) {
  const supabase = await getSupabase();
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: location.origin + "/connexion" },
  });
  if (error) throw error;
}

export async function motDePasseOublie(email) {
  const supabase = await getSupabase();
  const { error } = await supabase.auth.resetPasswordForEmail(email, { redirectTo: location.origin + "/connexion" });
  if (error) throw error;
}

export async function deconnexion() {
  const supabase = await getSupabase();
  await supabase.auth.signOut();
}

// Appel à l'API DarkCodex avec le jeton de la session en cours.
export async function apiFetch(chemin, options = {}) {
  const session = await getSession();
  if (!session) throw new Error("Vous n'êtes pas connecté.");
  const headers = { ...(options.headers || {}), Authorization: "Bearer " + session.access_token };
  if (options.body) headers["Content-Type"] = "application/json";
  return fetch(chemin, { ...options, headers });
}

// À appeler en haut d'une page privée : renvoie la session, ou redirige vers /connexion.
export async function exigerConnexion() {
  const session = await getSession();
  if (!session) {
    memoriserRetour(location.pathname + location.search);
    location.replace("/connexion");
    return null;
  }
  return session;
}
