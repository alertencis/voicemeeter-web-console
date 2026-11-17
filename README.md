# 🎚️ Voicemeeter Web Console

**Console de mixage web pour Voicemeeter, via VBAN.**

Pilotez **Voicemeeter Basic, Banana ou Potato** depuis un téléphone, une tablette ou un PC, sur votre réseau local — sans rien installer sur l'appareil de contrôle : un simple navigateur suffit.

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Windows](https://img.shields.io/badge/Windows-OK-0078D6?logo=windows&logoColor=white)
![Linux](https://img.shields.io/badge/Linux-OK-FCC624?logo=linux&logoColor=black)
![macOS](https://img.shields.io/badge/macOS-OK-000000?logo=apple&logoColor=white)

## ✨ Fonctionnalités

- Faders verticaux avec vumètres, **Couper / Rétablir**, routage A/B, Mono, Solo, M.C., K
- Boutons **−** / **+** pour un réglage au dB près (appui long = répétition)
- Égaliseur 6 bandes sur les bus, Comp / Gate, pan, égaliseur 3 bandes selon la voie
- Renommage des voies (envoyé aussi à Voicemeeter) et des PC
- Jusqu'à **20 PC Voicemeeter**, un onglet par PC
- Mise en page automatique : PC, tablette, téléphone, plein écran, thème clair / sombre
- **Un seul fichier Python**, une seule dépendance (`vban-cmd`)

## ⚙️ Comment ça marche

```
 Téléphone / tablette / PC          Serveur (Windows, Linux, macOS)          PC Voicemeeter (Windows)
       navigateur  ──── HTTP 8090 ────►  vol_server.py  ──── VBAN-TEXT UDP 6980 ────►  Voicemeeter
```

Le serveur peut tourner sur n'importe quelle machine du réseau, **y compris sur le PC Voicemeeter lui-même** (dans ce cas, IP = `127.0.0.1`).

---

## 1. Préparer Voicemeeter (sur le PC Windows)

À faire une fois par PC Voicemeeter.

1. Dans Voicemeeter, ouvrir **Menu → VBAN** et cocher **VBAN ON**.
2. Dans **Incoming Streams**, configurer une ligne :
   - **Stream name** : `Command1`
   - **IP Address From** : l'IP du serveur (ou `127.0.0.1` si c'est le même PC)
   - **Port** : `6980`
   - Activer la ligne (**ON**)
3. Autoriser le **port UDP 6980** en entrée dans le pare-feu Windows.

> 💡 Donnez une IP fixe (ou une réservation DHCP) au serveur et au PC Voicemeeter pour ne pas tout reconfigurer.

---

## 2. Installer et lancer le serveur

Il faut **Python 3.10 ou plus récent**. Téléchargez le projet (bouton **Code → Download ZIP**) ou :

```bash
git clone https://github.com/alertencis/voicemeeter-web-console.git
```

### 🪟 Windows

1. Installer Python depuis [python.org](https://www.python.org/downloads/) en cochant **Add python.exe to PATH**.
2. Double-cliquer sur **`start.bat`**.

Au premier lancement, `start.bat` crée l'environnement Python et installe `vban-cmd` automatiquement. Les fois suivantes, il démarre directement.
Si Windows demande l'autorisation réseau, cochez **Réseaux privés**.

<details>
<summary>Démarrage automatique à l'ouverture de session</summary>

Planificateur de tâches → **Créer une tâche** :

- Déclencheur : **À l'ouverture de session**
- Action : démarrer `C:\chemin\du\projet\venv\Scripts\pythonw.exe`
- Arguments : `vol_server.py`
- Démarrer dans : `C:\chemin\du\projet`

(`pythonw.exe` lance le serveur sans fenêtre. Lancez `start.bat` une première fois pour créer le `venv`.)
</details>

### 🐧 Linux (Ubuntu, Debian, Raspberry Pi OS…)

```bash
sudo apt install -y python3 python3-venv
cd voicemeeter-web-console
sh start.sh
```

Si le pare-feu `ufw` est actif : `sudo ufw allow 8090/tcp`

<details>
<summary>Démarrage automatique (service systemd)</summary>

Lancez `sh start.sh` une première fois, puis créez `/etc/systemd/system/vol.service`
(remplacez `VOTRE_UTILISATEUR` et le chemin) :

```ini
[Unit]
Description=Console Voicemeeter
After=network-online.target
Wants=network-online.target

[Service]
User=VOTRE_UTILISATEUR
WorkingDirectory=/home/VOTRE_UTILISATEUR/voicemeeter-web-console
ExecStart=/home/VOTRE_UTILISATEUR/voicemeeter-web-console/venv/bin/python vol_server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vol.service
journalctl -u vol.service -f      # voir les logs
```
</details>

### 🍎 macOS

```bash
brew install python      # si Python 3.10+ n'est pas déjà installé
cd voicemeeter-web-console
sh start.sh
```

### Installation manuelle (toutes plateformes)

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt        # Windows : venv\Scripts\pip install -r requirements.txt
venv/bin/python vol_server.py                   # Windows : venv\Scripts\python vol_server.py
```

---

## 3. Utilisation

1. Sur le téléphone / la tablette, ouvrir **`http://IP_DU_SERVEUR:8090`**
   (IP du serveur : `ipconfig` sous Windows, `hostname -I` sous Linux, `ipconfig getifaddr en0` sous macOS).
2. Cliquer sur **⚙** en haut à droite et renseigner l'**IP du PC Voicemeeter**, le flux (`Command1`) et la **version** (Basic / Banana / Potato).
3. C'est prêt. Ajoutez d'autres PC avec **+ Ajouter un PC**.

> 📱 Astuce : « Ajouter à l'écran d'accueil » depuis le navigateur pour l'utiliser comme une appli.

### Sur chaque voie

| Action | Geste |
|---|---|
| Régler le volume | Glisser le fader (clavier : flèches) |
| Ajuster finement | Boutons **−** / **+** (appui long = répétition) |
| Revenir à 0 dB | Double-clic sur le fader ou sur la valeur en dB |
| Couper / Rétablir | Grand bouton **Couper** |
| Renommer | Cliquer sur le nom de la voie |
| Routage A / B, Mono, Solo, M.C., K | Boutons sous « Couper » |
| Pan, égaliseur, Comp / Gate | Menus « Réglages » / « Égaliseur » de la voie |

### En-tête

| Icône | Rôle |
|---|---|
| ⟳ | Reconnecter le PC affiché |
| ⛶ | Plein écran |
| ⚙ | Réglages (IP, flux, version, nom du PC, ajout / retrait de PC) |

La configuration est enregistrée dans `vol_config.json` (créé automatiquement, non versionné).

---

## 4. Personnalisation

### Logo et favicon

Déposez simplement un fichier à côté de `vol_server.py` :

- **Logo** : `logo.svg`, `logo.png`, `logo.webp`, `logo.jpg` ou `logo.gif` (hauteur ~60 px)
- **Favicon** : `favicon.svg`, `favicon.ico` ou `favicon.png` (32×32 ou 64×64)

Sans fichier, un badge texte est dessiné à la place. Ces fichiers sont ignorés par Git : votre logo reste chez vous.

### Constantes

| Où | Nom | Rôle | Défaut |
|---|---|---|---|
| `vol_server.py` | `WEB_PORT` | Port de la page web | `8090` |
| `vol_server.py` | `STREAM` | Nom du flux VBAN par défaut | `Command1` |
| `vol_server.py` | `DEFAULT_IP` | IP proposée au premier lancement | `192.168.1.50` |
| `vol_server.py` | `MAX_VM` | Nombre maximum de PC | `20` |
| `vol_server.py` | `BRAND_NAME` / `BRAND_SHORT` | Texte du badge / de la favicon sans fichier | `VM` |
| JS de la page | `STEP` | Pas des boutons − / + et des flèches (dB) | `1` |
| CSS de la page | `--in` / `--outc` | Couleurs des entrées / sorties | `#0f7b8a` / `#a55a08` |

Redémarrez le serveur après modification.

---

## 5. Dépannage

**La page s'ouvre mais rien ne bouge / « Valeurs : lecture impossible »**
- Dans Voicemeeter : VBAN activé, flux `Command1` actif, **IP Address From** = IP du serveur, port 6980.
- IP et version correctes dans ⚙ Réglages.
- Pare-feu du PC Voicemeeter : UDP 6980 autorisé.
- Bouton **⟳** pour forcer une reconnexion.

**Le nombre de voies est faux** — la version choisie ne correspond pas à Voicemeeter : changez-la dans Réglages.

**« Niveaux : indisponibles »** — mettez `vban-cmd` à jour : `venv/bin/pip install -U vban-cmd` (Windows : `venv\Scripts\pip install -U vban-cmd`).

**`ModuleNotFoundError: No module named 'vban_cmd'`** — lancez le serveur avec `start.bat` / `start.sh` (ou le Python du `venv`).

**`Address already in use`** — le serveur tourne déjà (autre fenêtre ou service). Arrêtez-le, ou changez `WEB_PORT`.

**Impossible d'ouvrir la page depuis la tablette** — même réseau (pas de Wi-Fi invité isolé), port TCP 8090 autorisé sur le serveur, bonne IP.

**`SyntaxError: Non-UTF-8 code`** — le fichier a été réenregistré dans un autre encodage : enregistrez `vol_server.py` en **UTF-8**.

---

## 🔒 Sécurité

La console n'a **pas de mot de passe** : toute personne qui accède au port 8090 peut modifier votre mixage.
Gardez-la sur un réseau local de confiance et **ne l'exposez pas sur Internet**. Pour un accès à distance, passez par un VPN (WireGuard, Tailscale…) ou un reverse-proxy avec authentification.

## Crédits

Basé sur [vban-cmd](https://github.com/onyx-and-iris/vban-cmd-python). Voicemeeter et VBAN sont des produits de [VB-Audio](https://vb-audio.com/).
