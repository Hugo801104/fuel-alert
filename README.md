# ⛽ Fuel Price Alert

> Trouvez automatiquement la station-service la moins chère près de chez vous et recevez une alerte quotidienne. / Automatically find the cheapest fuel station near you and get a daily alert.

[![Daily Fuel Price Check](https://img.shields.io/badge/CI-GitHub%20Actions-blue)](.github/workflows/daily_check.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## 🇫🇷 Français

### 📋 Présentation

**Fuel Price Alert** est un projet open-source qui :

1. **Récupère quotidiennement** les prix des carburants en France via l'[API Open Data officielle](https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/prix-des-carburants-en-france-flux-instantane-v2/) du Ministère de l'Économie.
2. **Identifie les trois meilleurs prix récents** dans un rayon défini autour d'un point GPS (mise à jour de moins de 3 jours).
3. **Envoie une alerte automatique** chaque matin (Telegram, Discord, Slack ou e-mail) via GitHub Actions.
4. **Fournit une interface web interactive** (Streamlit) pour configurer et tester le tout visuellement, avec carte OpenStreetMap.

### 🏗️ Architecture du projet

```
fuel-price-alert/
├── src/
│   ├── fetcher.py       # Appels API + pagination + gestion d'erreurs
│   ├── analyzer.py      # Distance haversine, filtrage, tri par prix
│   ├── notifier.py      # Construction et envoi des notifications (Apprise)
│   └── subscriptions.py # Abonnements quotidiens Supabase
├── app.py               # Interface web Streamlit
├── main.py              # Point d'entrée CLI (exécuté par le cron GitHub Actions)
├── supabase/schema.sql   # Schéma des abonnements et du journal d'envoi
├── .github/workflows/
│   └── daily_check.yml  # Workflow cron quotidien (7h00 UTC)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── .gitignore
```

### ⚙️ Installation locale

**Prérequis** : Python 3.11+

```bash
git clone https://github.com/<votre-utilisateur>/fuel-price-alert.git
cd fuel-price-alert

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Éditez .env avec vos coordonnées GPS et votre canal de notification
```

### 🧪 Exécuter les tests

```bash
python -m pytest -q
```

Les tests se trouvent dans `tests/` et sont exécutés automatiquement avant
la recherche quotidienne dans GitHub Actions.

### 🖥️ Lancer l'interface web

```bash
streamlit run app.py
```

L'application s'ouvre sur `http://localhost:8501`. Vous pouvez :
- cliquer sur la carte ou saisir une adresse pour définir le point de recherche ;
- choisir le carburant (`E10`, `SP95`, `SP98`, `Gazole`, `E85`, `GPLc`) et le rayon ;
- configurer un canal de notification (Telegram, Discord, Slack, e-mail) ;
- cliquer sur **"Lancer la recherche maintenant"** pour un test en direct ;
- exporter la configuration finale en fichier `.env` téléchargeable.

### 🤖 Exécution en ligne de commande (CRON)

```bash
python main.py
```

Le script lit sa configuration depuis les variables d'environnement (`.env` en local) et retourne un code de sortie :

| Code | Signification |
|------|----------------|
| `0` | Succès |
| `1` | Erreur de configuration (variables manquantes/invalides) |
| `2` | Erreur API (source de données injoignable) |
| `3` | Aucune station trouvée dans le rayon donné |
| `4` | Échec de l'envoi de la notification |

### 🔔 Abonnements quotidiens Telegram

L'interface Streamlit permet à un visiteur de s'inscrire pour recevoir une
notification Telegram quotidienne pendant 10 jours (durée réglable jusqu'à
30 jours). Le visiteur saisit son `chat_id` Telegram ; l'abonnement est stocké
dans Supabase et traité chaque matin par GitHub Actions.

#### Configuration Supabase

1. Créez un projet sur [Supabase](https://supabase.com/).
2. Ouvrez **SQL Editor** et exécutez le contenu de `supabase/schema.sql`.
3. Récupérez **Project URL** dans *Project Settings → Data API*.
	Utilisez uniquement une URL comme `https://xxxx.supabase.co`, sans ajouter
	`/rest/v1` : le client Python ajoute lui-même ce chemin.
4. Récupérez la clé **service_role** dans *Project Settings → API*.
5. Créez un bot avec [@BotFather](https://t.me/BotFather) et conservez son token.
6. Configurez ces secrets dans Streamlit Cloud (*App settings → Secrets*) :

```toml
SUPABASE_URL = "https://xxxx.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "votre-cle-service-role"
TELEGRAM_BOT_TOKEN = "123456789:AA..."
```

Ajoutez les mêmes noms dans **GitHub → Settings → Secrets and variables →
Actions**. La clé `service_role` ne doit jamais être publiée dans le code ou
dans le navigateur.

Pour trouver un `chat_id`, un utilisateur peut ouvrir [@userinfobot](https://t.me/userinfobot)
dans Telegram. Le workflow GitHub Actions traite ensuite tous les abonnements
actifs chaque jour à 07:00 UTC et les désactive automatiquement à expiration.

### 🔁 Automatisation via GitHub Actions

Le workflow [`.github/workflows/daily_check.yml`](.github/workflows/daily_check.yml) exécute `main.py` **tous les jours à 7h00 UTC**, et peut aussi être déclenché manuellement depuis l'onglet **Actions** de votre dépôt (`workflow_dispatch`).

#### 🔐 Configuration des GitHub Secrets

Dans votre dépôt : **Settings → Secrets and variables → Actions → New repository secret**, ajoutez :

| Secret | Exemple | Description |
|--------|---------|--------------|
| `SUPABASE_URL` | `https://xxxx.supabase.co` | URL du projet Supabase |
| `SUPABASE_SERVICE_ROLE_KEY` | `eyJ...` | Clé serveur Supabase, gardée secrète |
| `TELEGRAM_BOT_TOKEN` | `123456789:AA...` | Token du bot Telegram |
| `LATITUDE` | `48.8566` | Latitude du point de recherche |
| `LONGITUDE` | `2.3522` | Longitude du point de recherche |
| `RADIUS_KM` | `5` | Rayon de recherche en km |
| `FUEL_TYPE` | `Gazole` | `E10`, `SP95`, `SP98`, `Gazole`, `E85` ou `GPLc` |
| `NOTIFICATION_URLS` | `tgram://123:ABC/456` | Une ou plusieurs URLs [Apprise](https://github.com/caronc/apprise), séparées par des virgules |

Le workflow utilise les secrets Supabase pour traiter les abonnements stockés
dans Supabase. `TELEGRAM_BOT_TOKEN` est requis si un abonnement Telegram est
actif. Les paramètres de recherche et
`NOTIFICATION_URLS` servent à l'exécution CLI directe lorsque le mode
abonnements n'est pas activé.

### Protocole d'inscription aux alertes

Le choix du canal se fait dans le formulaire principal de l'application. Un
abonnement dure de 1 à 30 jours et les données d'abonnement sont supprimées à
la fin de la durée.

#### Telegram

1. Ouvrez le bot Telegram utilisé par l'application.
2. Appuyez sur **Démarrer** ou envoyez `/start` avant de vous inscrire.
3. Récupérez votre identifiant numérique avec `@userinfobot`.
4. Choisissez **Telegram**, saisissez cet identifiant dans **Chat ID Telegram**,
	acceptez la notification, puis validez.
5. Pour un groupe, ajoutez d'abord le bot au groupe et utilisez l'identifiant
	du groupe, généralement au format `-100...`.

Le bot doit être celui correspondant au secret `TELEGRAM_BOT_TOKEN`. Un Chat ID
provenant d'un autre bot provoque l'erreur `chat not found`.

#### Discord

1. Ouvrez le serveur Discord et le salon qui doit recevoir les alertes.
2. Ouvrez **Modifier le salon → Intégrations → Webhooks**.
3. Créez un webhook, copiez son URL complète et ne la partagez pas.
4. Choisissez **Discord**, collez l'URL dans **URL du webhook Discord**,
	acceptez la notification, puis validez.

L'URL doit commencer par `https://discord.com/api/webhooks/`. Elle est
supprimée avec l'abonnement à son expiration.

> 💡 L'onglet **"Export de la configuration"** de l'interface Streamlit génère directement ces valeurs pour vous.

### 📢 Configurer les canaux de notification (Apprise)

| Canal | Format d'URL |
|-------|--------------|
| Telegram | `tgram://{bot_token}/{chat_id}` |
| Discord | `discord://{webhook_id}/{webhook_token}` |
| Slack | `slack://{token_a}/{token_b}/{token_c}` |
| E-mail (SMTP) | `mailtos://{user}:{password}@{smtp_host}?to={destination_email}` |

Voir la [documentation Apprise](https://github.com/caronc/apprise/wiki) pour la liste complète des ~90 services supportés.

### 🐳 Déploiement avec Docker

```bash
docker compose up --build -d
```

L'interface sera accessible sur `http://localhost:8501`. Le fichier `.env` (à la racine, non commité) est chargé automatiquement via `env_file` dans `docker-compose.yml`.

### ☁️ Déploiement de l'interface web

- **Streamlit Community Cloud** : connectez votre dépôt GitHub sur [share.streamlit.io](https://share.streamlit.io), pointez vers `app.py`, et ajoutez vos secrets dans *App settings → Secrets* (format TOML).
- **Hugging Face Spaces** : créez un Space de type *Streamlit*, poussez le code (le `Dockerfile` fourni est aussi compatible avec un Space *Docker*), et renseignez vos variables dans *Settings → Repository secrets*.
- **Serveur privé** : utilisez `docker-compose.yml` derrière un reverse-proxy (Nginx/Traefik) avec HTTPS.

### 🧪 Gestion des erreurs

Le projet gère explicitement :
- les **timeouts** et erreurs réseau de l'API (retries automatiques avec backoff) ;
- l'**absence de station** dans le rayon donné (`NoStationFoundError`) ;
- la **rupture de stock** d'un carburant à une station donnée (prix `null` ignoré) ;
- les **URLs de notification invalides ou manquantes**.

### 🤝 Contribuer

Les *pull requests* sont bienvenues ! Merci de respecter le typage (`type hints`), les docstrings Google Style, et d'ajouter des tests pour toute nouvelle logique métier.

### 📄 Licence

Projet distribué sous licence MIT. Voir le fichier `LICENSE`.

---

## 🇬🇧 English

### 📋 Overview

**Fuel Price Alert** is an open-source project that:

1. **Fetches French fuel prices daily** via the official [Open Data API](https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/prix-des-carburants-en-france-flux-instantane-v2/) of the French Ministry of Economy.
2. **Finds the cheapest station** within a defined radius around a GPS point.
3. **Sends an automatic daily alert** (Telegram, Discord, Slack or e-mail) via GitHub Actions.
4. **Provides an interactive web UI** (Streamlit) with an OpenStreetMap map to configure and test everything visually.

### 🏗️ Project structure

See the French section above — the structure is identical (`src/fetcher.py`, `src/analyzer.py`, `src/notifier.py`, `app.py`, `main.py`, `.github/workflows/daily_check.yml`, `Dockerfile`, `docker-compose.yml`).

### ⚙️ Local installation

**Requirement**: Python 3.11+

```bash
git clone https://github.com/<your-username>/fuel-price-alert.git
cd fuel-price-alert

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env with your GPS coordinates and notification channel
```

### 🖥️ Run the web interface

```bash
streamlit run app.py
```

Open `http://localhost:8501`. You can:
- click on the map or type an address to set the search point;
- choose the fuel type (`E10`, `SP95`, `SP98`, `Gazole`, `E85`, `GPLc`) and the search radius;
- configure a notification channel (Telegram, Discord, Slack, e-mail);
- click **"Run search now"** to test live results;
- export the final configuration as a downloadable `.env` file.

### 🤖 Command-line / CRON execution

```bash
python main.py
```

Exit codes: `0` success, `1` config error, `2` API error, `3` no station found, `4` notification failure.

### 🔁 GitHub Actions automation

The [`.github/workflows/daily_check.yml`](.github/workflows/daily_check.yml) workflow runs `main.py` **every day at 7:00 AM UTC** and supports manual triggering (`workflow_dispatch`).

#### 🔐 Setting up GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret** and add: `LATITUDE`, `LONGITUDE`, `RADIUS_KM`, `FUEL_TYPE`, `NOTIFICATION_URLS` (see the French table above for examples — same keys and formats apply).

### 📢 Notification channels (Apprise)

Same URL formats as listed in the French section (Telegram, Discord, Slack, SMTP e-mail). Full list of ~90 supported services: [Apprise wiki](https://github.com/caronc/apprise/wiki).

### 🐳 Docker deployment

```bash
docker compose up --build -d
```

### ☁️ Web app deployment options

- **Streamlit Community Cloud**: connect your GitHub repo at [share.streamlit.io](https://share.streamlit.io), point to `app.py`, add secrets under *App settings → Secrets* (TOML format).
- **Hugging Face Spaces**: create a *Streamlit* (or *Docker*, using the provided `Dockerfile`) Space, push the code, set variables under *Settings → Repository secrets*.
- **Private server**: run `docker-compose.yml` behind a reverse proxy (Nginx/Traefik) with HTTPS.

### 🧪 Error handling

The project explicitly handles API timeouts/network errors (automatic retries with backoff), no station found in radius, fuel out of stock at a given station, and invalid/missing notification URLs.

### 🤝 Contributing

Pull requests welcome! Please keep type hints, Google-style docstrings, and add tests for new business logic.

### 📄 License

MIT License — see `LICENSE`.
