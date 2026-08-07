# Maouloud 2026 — Application de l'association

Application web installable comme une application mobile (PWA) pour gérer les
cotisations, dépenses, bonnes volontés et bus de l'association.

- **Backend** : FastAPI + SQLite (fichier unique, aucune base à installer).
- **Frontend** : une seule page HTML/CSS/JS, installable sur l'écran d'accueil
  d'un téléphone (Android et iOS) comme une vraie application.
- **Comptes nominatifs** : chaque collecteur a son propre identifiant et mot
  de passe. Deux rôles : **Administrateur** (gère les comptes) et
  **Collecteur** (saisit les cotisations, dépenses, etc.).
- **Journal d'activité** : chaque connexion et chaque modification est
  enregistrée avec le nom de la personne — visible par les administrateurs
  dans l'onglet *Comptes*.
- **Consultation publique** : sans connexion, tout le monde peut consulter
  (lecture seule) — c'est le mode "Membre".

Les données de votre fichier Excel d'origine sont préchargées automatiquement
au premier démarrage (248 membres, dépenses, bonnes volontés, mobile money,
les 4 bus SEFA Pikine → Barobé Diackel).

---

## 1. Avant tout déploiement

Copiez `.env.example` en `.env` et changez le mot de passe administrateur :

```
ADMIN_USERNAME=admin
ADMIN_PASSWORD=un-mot-de-passe-solide-et-different
```

Ce compte admin est créé automatiquement au premier démarrage. Connectez-vous
avec, puis créez un compte pour chaque collecteur depuis l'onglet **Comptes**
(visible uniquement pour les administrateurs) — inutile de repartager le mot
de passe admin avec tout le monde.

Sans HTTPS, mettez `COOKIE_SECURE=false` (uniquement pour des tests en local).
En production, laissez `COOKIE_SECURE=true` — la quasi-totalité des
hébergeurs cloud fournissent un certificat HTTPS gratuit automatiquement.

---

## 2. Tester en local

```bash
pip install -r requirements.txt
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD=test1234
export COOKIE_SECURE=false
uvicorn app.main:app --reload --port 8000
```

Ouvrez `http://localhost:8000`. Cliquez sur **Se connecter** avec les
identifiants ci-dessus pour accéder à la saisie ; sans connexion, vous êtes en
consultation seule.

---

## 3. Installer comme application sur un téléphone (PWA)

Une fois l'application déployée avec une URL HTTPS (voir section 4) :

- **Android (Chrome)** : ouvrir l'URL → menu ⋮ → *Ajouter à l'écran d'accueil*.
- **iPhone (Safari)** : ouvrir l'URL → bouton Partager → *Sur l'écran d'accueil*.

L'icône apparaît alors comme une vraie application, en plein écran, sans
barre d'adresse. C'est la même application web, pas besoin de passer par
l'App Store ou le Play Store.

> Publier une application *native* sur l'App Store / Google Play (avec compte
> développeur, révision Apple/Google, etc.) est un projet séparé et plus
> lourd. Si vous voulez aller jusque-là, c'est le bon moment pour prendre le
> relais avec **Claude Code**, qui peut empaqueter cette même application
> avec Capacitor et vous accompagner pas à pas dans la publication.

---

## 4. Déployer sur un hébergeur cloud gratuit/économique

### Option A — Render.com (le plus simple, offre gratuite disponible)

1. Créez un dépôt Git (GitHub/GitLab) avec ce dossier et poussez-le.
2. Sur [render.com](https://render.com) : **New → Web Service**, connectez le dépôt.
3. Render détecte le `Dockerfile` automatiquement — laissez les réglages par défaut.
4. Dans **Environment**, ajoutez `ADMIN_USERNAME` et `ADMIN_PASSWORD` (laissez `COOKIE_SECURE=true`).
5. Dans **Disks**, ajoutez un disque persistant monté sur `/app/data`
   (indispensable pour ne pas perdre les comptes et les données à chaque
   redéploiement).
6. Déployez. Render fournit une URL HTTPS `https://votre-app.onrender.com`.

### Option B — Railway.app

1. **New Project → Deploy from GitHub repo**, sélectionnez ce dépôt.
2. Railway détecte le `Dockerfile` automatiquement.
3. Onglet **Variables** : ajoutez `ADMIN_USERNAME` et `ADMIN_PASSWORD`.
4. Onglet **Volumes** : ajoutez un volume monté sur `/app/data`.
5. Railway fournit une URL HTTPS automatiquement.

### Option C — Fly.io

```bash
fly launch          # détecte le Dockerfile, répondez aux questions
fly volumes create maouloud_data --size 1
fly secrets set ADMIN_USERNAME=admin ADMIN_PASSWORD="votre-mot-de-passe"
fly deploy
```
Montez le volume sur `/app/data` dans `fly.toml` (`[mounts]`) généré par `fly launch`.

---

## 5. Gestion des comptes collecteurs

Une fois connecté en tant qu'administrateur, l'onglet **Comptes** permet de :
- créer un compte pour chaque collecteur (identifiant, nom affiché, mot de passe, rôle) ;
- réinitialiser le mot de passe d'un compte ;
- supprimer un compte (impossible de supprimer le dernier administrateur, ni son propre compte en cours d'utilisation) ;
- consulter le **journal d'activité** : qui s'est connecté, qui a modifié quelles données, et quand.

---

## 6. Sauvegardes

Toutes les données (comptes, cotisations, dépenses...) vivent dans un seul
fichier SQLite (`data.db`). Pour sauvegarder : copiez ce fichier régulièrement
(ou le volume Docker/hébergeur qui le contient). Aucune autre base ni service
externe n'est nécessaire.

---

## 7. Structure du projet

```
maouloud2026-app/
├── app/
│   ├── main.py          # API FastAPI (comptes, auth, stockage JSON, journal)
│   └── seed_data.json   # Données initiales extraites du fichier Excel
├── static/
│   ├── index.html       # Application web complète (frontend)
│   ├── manifest.json    # Manifeste PWA (installation mobile)
│   ├── service-worker.js
│   └── icons/
├── requirements.txt
├── Dockerfile
└── .env.example
```

## 8. Limites connues de cette version

- Pas de sauvegarde automatique programmée — à mettre en place selon
  l'hébergeur choisi (la plupart proposent des snapshots de volume).
- Base SQLite adaptée à l'usage d'une association (usage concurrent faible) ;
  au-delà de quelques dizaines d'utilisateurs simultanés en écriture, prévoir
  une migration vers PostgreSQL.
- Pas de récupération de mot de passe par email (l'administrateur réinitialise
  manuellement depuis l'onglet Comptes).
