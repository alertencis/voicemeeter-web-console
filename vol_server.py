#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Console web Voicemeeter (Basic / Banana / Potato) pilotée en VBAN-TEXT.

Lancer : python vol_server.py, puis ouvrir http://IP_DU_SERVEUR:8090
Jusqu'à 20 PC Voicemeeter (un onglet par PC). IP, flux VBAN et version se règlent dans la page.
La configuration est enregistrée dans vol_config.json, à côté du script.
"""
import ipaddress, json, mimetypes, os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
import vban_cmd

STREAM, WEB_PORT, DEFAULT_IP, MAX_VM = "Command1", 8090, "192.168.1.50", 20
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(SCRIPT_DIR, "vol_config.json")
BRAND_NAME = "VM"   # texte du badge d'en-tête quand il n'y a pas de fichier logo
BRAND_SHORT = "VM"  # 2-3 lettres pour la favicon quand il n'y a pas de fichier favicon
# Fichiers que le serveur cherche dans son dossier pour le logo et la favicon (dans l'ordre, le premier trouve l'emporte)
LOGO_NAMES = ("logo.svg", "logo.png", "logo.webp", "logo.jpg", "logo.jpeg", "logo.gif")
FAVICON_NAMES = ("favicon.svg", "favicon.ico", "favicon.png")


# S'assure que .ico, .svg et .webp ont le bon mime-type (certains systemes manquent les mappings)
for _ext, _mt in (("ico", "image/x-icon"), ("svg", "image/svg+xml"), ("webp", "image/webp")):
    mimetypes.add_type(_mt, "." + _ext)


def asset_path(names):
    """Retourne le chemin absolu du premier fichier trouve dans le dossier du script, ou None."""
    for n in names:
        p = os.path.join(SCRIPT_DIR, n)
        if os.path.isfile(p):
            return p
    return None


def fallback_svg(text, w, h, fsize):
    """SVG de secours quand il n'y a pas de fichier logo/favicon : rectangle teal + texte blanc."""
    safe = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">'
            f'<rect width="{w}" height="{h}" rx="{min(w, h) // 4}" fill="#0f7b8a"/>'
            f'<text x="{w // 2}" y="{int(h * 0.68)}" text-anchor="middle" fill="#fff" '
            f'font-family="system-ui,sans-serif" font-weight="800" font-size="{fsize}">{safe}</text>'
            f'</svg>')
# entrées matérielles, entrées virtuelles, bus A, bus B
LAYOUT = {"basic": (2, 1, 1, 1), "banana": (3, 2, 3, 2), "potato": (5, 3, 5, 3)}
KNOBS = {"pan": ("Pan_x", 1 / 200, -100, 100), "eq1": ("EQGain1", 1, -12, 12),
         "eq2": ("EQGain2", 1, -12, 12), "eq3": ("EQGain3", 1, -12, 12),
         "comp": ("Comp", 1, 0, 10), "gate": ("Gate", 1, 0, 10),
         "audib": ("Audibility", 1, 0, 10), "cx": ("Color_x", 1 / 200, -100, 100),
         "cy": ("Color_y", 1 / 200, -100, 100), "py": ("Pan_y", 1 / 200, -100, 100)}
# Basic n'a ni Comp/Gate/Audibilité ni égaliseur : on n'affiche que ce qui existe
HW = {"basic": ["pan", "cx", "cy"], "banana": ["pan", "cx", "cy", "audib", "comp", "gate"]}
VIRT = {"basic": ["pan"], "banana": ["pan", "py", "eq1", "eq2", "eq3"]}
HW["potato"], VIRT["potato"] = HW["banana"], VIRT["banana"]
BUS_FLAGS = {"basic": ["mono"], "banana": ["mono", "eq.on"], "potato": ["mono", "eq.on"]}
EXTRA = ["mc", "karaoke"]  # boutons M.C. et K des 2 premières entrées virtuelles (Banana, Potato)
EQ_BANDS = 6
EQ_PARAM = {"g": ("gain", -12.0, 12.0), "f": ("f", 20.0, 20000.0)}
lock = threading.Lock()
vms = []


def channels(kind):
    hw, vi, na, nb = LAYOUT[kind]
    ch = [(f"i{i}", f"Entrée {i + 1}", "strip", i) for i in range(hw)]
    ch += [(f"i{hw + i}", f"Virtuel {i + 1}", "strip", hw + i) for i in range(vi)]
    ch += [(f"a{i + 1}", f"A{i + 1}", "bus", i) for i in range(na)]
    ch += [(f"b{i + 1}", f"B{i + 1}", "bus", na + i) for i in range(nb)]
    return ch


def flag_get(z, path):
    for part in path.split("."):
        z = getattr(z, part)
    return bool(z)


def flag_set(z, path, val):
    parts = path.split(".")
    for part in parts[:-1]:
        z = getattr(z, part)
    setattr(z, parts[-1], val)


def to_db(v):
    if isinstance(v, (list, tuple)):
        v = max(v) if v else -60
    return max(-60.0, min(6.0, float(v)))


class VM:
    """Un PC Voicemeeter joint en VBAN, avec son propre état et ses propres noms de voies."""

    def __init__(self, ip, stream=STREAM, kind="banana", names=None, label=""):
        self.ip, self.stream, self.kind, self.names = ip, stream, kind, dict(names or {})
        self.label = str(label or "").strip()[:40]  # nom personnalise du PC, affiche dans les onglets ("" = PC 1 / 2 / 3)
        self.ctx = self.vban = None
        self.raw = {}  # dernières valeurs lues dans Voicemeeter (détection des lectures périmées)
        hw, vi, na, nb = LAYOUT[kind]
        ch = channels(kind)
        self.info = {k: (kd, i) for k, _, kd, i in ch}
        self.base = {k: n for k, n, _, _ in ch}
        sflags = [f"A{i + 1}" for i in range(na)] + [f"B{i + 1}" for i in range(nb)] + ["mono", "solo"]
        self.state = {}
        for k, n, kd, i in ch:
            virt = kd == "strip" and i >= hw
            if kd == "bus":
                f, kn = BUS_FLAGS[kind], []
            else:
                extra = [EXTRA[i - hw]] if virt and kind != "basic" and i - hw < len(EXTRA) else []
                f, kn = sflags + extra, (VIRT if virt else HW)[kind]
            self.state[k] = {"name": self.names.get(k) or n, "gain": 0.0, "mute": False, "level": -60.0,
                             "f": {p: False for p in f}, "k": {q: 0.0 for q in kn}}
            if kd == "bus" and kind != "basic":
                self.state[k]["eq"] = [{"g": 0.0, "f": 0} for _ in range(EQ_BANDS)]
        self.diag = {"levels": "pas encore lu", "values": "pas encore lu"}

    def conf(self):
        return {"ip": self.ip, "stream": self.stream, "kind": self.kind, "names": self.names, "label": self.label}

    def close(self):
        if self.ctx is not None:
            try:
                self.ctx.__exit__(None, None, None)
            except Exception:
                pass
        self.ctx = self.vban = None

    def connect(self):
        self.close()
        self.raw = {}  # resynchronise tout à la (re)connexion
        kw = dict(ip=self.ip, port=6980, streamname=self.stream)
        try:
            self.ctx = vban_cmd.api(self.kind, subs={"pdirty": True, "ldirty": True}, **kw)
        except TypeError:
            self.ctx = vban_cmd.api(self.kind, **kw)
        self.vban = self.ctx.__enter__()

    def zone(self, k):
        if self.vban is None:
            raise RuntimeError("pas connecté")
        kind, i = self.info[k]
        return getattr(self.vban, kind)[i]

    def send_param(self, k, name, val):
        """Envoie n'importe quel paramètre Voicemeeter en VBAN-TEXT (ex: EQ.channel[0].cell[1].gain)."""
        kind, i = self.info[k]
        z = self.zone(k)
        try:
            self.vban.sendtext(f"{kind.capitalize()}[{i}].{name}={val};")
        except AttributeError:
            setattr(z, name.lower(), val)

    def poll(self, values):
        st = self.state
        try:
            for k in st:
                z = self.zone(k)
                lv = z.levels.prefader if self.info[k][0] == "strip" else z.levels.all
                st[k]["level"] = to_db(lv)
            self.diag["levels"] = "ok"
        except Exception as e:
            self.diag["levels"] = f"indisponibles ({e})"
        if not values:
            return
        try:
            for k in st:
                z = self.zone(k)
                # On n'adopte une valeur lue que si elle a changé depuis la lecture précédente :
                # identique = lecture périmée, on garde ce qui a été envoyé depuis la console.
                g, m = round(float(z.gain), 1), bool(z.mute)
                if self.raw.get((k, "g")) != g:
                    st[k]["gain"] = g
                if self.raw.get((k, "m")) != m:
                    st[k]["mute"] = m
                self.raw[(k, "g")], self.raw[(k, "m")] = g, m
                for p in st[k]["f"]:
                    try:
                        val = flag_get(z, p)
                    except Exception:
                        continue
                    if self.raw.get((k, p)) != val:
                        st[k]["f"][p] = val
                    self.raw[(k, p)] = val
                lab = str(getattr(z, "label", "") or "").strip()
                st[k]["name"] = self.names.get(k) or (f"{self.base[k]} : {lab}" if lab else self.base[k])
            self.diag["values"] = "ok"
        except Exception as e:
            self.diag["values"] = f"lecture impossible ({e})"


def save_conf():
    with open(CONF, "w", encoding="utf-8") as fh:
        json.dump({"vms": [v.conf() for v in vms]}, fh)


def poll():
    n = 0
    while True:
        time.sleep(0.15)
        n += 1
        with lock:
            for v in vms:
                v.poll(n % 10 == 0)


PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Console Voicemeeter</title>
<link rel="icon" href="?r=favicon" sizes="any">
<link rel="shortcut icon" href="?r=favicon">
<link rel="apple-touch-icon" href="?r=logo">
<style>
:root{--bg:#dfe4e8;--panel:#f5f7f8;--ink:#18242d;--soft:#5c6b76;--acc:#0f7b8a;--ok:#2f9e5b;--warn:#e0a11b;--mute:#c62f3b;
/* Couleurs des deux groupes : --in = entrées, --outc = sorties, --tint = intensité de la teinte des cases,
   --pint = intensité du fond du panneau, --gap = écart entre panneaux entrées / sorties */
--in:#0f7b8a;--outc:#a55a08;--tint:18%;--pint:11%;--gap:28px}
@media(prefers-color-scheme:dark){:root{--bg:#10181e;--panel:#1b262e;--ink:#eef2f4;--soft:#93a3ae;--acc:#3cc0d1;--ok:#3fbf75;--warn:#f0b73a;--mute:#ef5a66;--in:#3cc0d1;--outc:#f0a04a}}
[hidden]{display:none!important}*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%;background:var(--bg);scrollbar-gutter:stable}
html:fullscreen{overflow:auto}
html:-webkit-full-screen{overflow:auto}
body{margin:0;padding:max(10px,env(safe-area-inset-top)) max(10px,env(safe-area-inset-right)) max(10px,env(safe-area-inset-bottom)) max(10px,env(safe-area-inset-left));background:var(--bg);color:var(--ink);font:16px/1.3 Bahnschrift,"Segoe UI",system-ui,sans-serif}
main{max-width:1840px;margin:0 auto}header{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:6px 10px;position:sticky;top:0;z-index:5;background:var(--bg);padding:4px 0}h1{margin:6px 0;font-size:19px;display:inline-flex;align-items:center;gap:10px;font-weight:600;letter-spacing:.01em}
/* Badge devant le titre : largeur AUTO pour que n'importe quel texte tienne.
   Si c'est une balise <img> (fichier logo trouvé), on neutralise fond et padding : l'image fournit son propre rendu. */
.brand{height:28px;min-width:28px;padding:0 10px;flex:none;border-radius:8px;background:var(--acc);display:inline-flex;align-items:center;justify-content:center;color:var(--panel);font-weight:800;font-size:13px;letter-spacing:.02em;box-shadow:0 1px 3px rgba(0,0,0,.15);white-space:nowrap;max-width:220px;overflow:hidden;text-overflow:ellipsis}
img.brand{padding:0;background:transparent;object-fit:contain;width:auto;max-width:220px}
body.compact .brand{height:22px;min-width:22px;padding:0 8px;font-size:11px;border-radius:6px}
.hb{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}
#tabs{display:flex;gap:8px;overflow-x:auto;margin:4px 0 8px}.tab{flex:none;display:flex;flex-direction:column;justify-content:center;line-height:1.15;background:var(--panel)}
.tab small{font-weight:400;font-size:12px;color:var(--soft)}.tab.on{background:var(--acc);color:var(--bg)}.tab.on small{color:inherit}
/* Tableau : une seule ligne (toutes les voies côte à côte) quand la largeur le permet, sinon empilé */
.board{display:grid;grid-template-columns:minmax(0,1fr);row-gap:var(--gap);column-gap:8px}
.board>section{min-width:0;position:relative;border-radius:16px;padding:4px 10px 2px;background:var(--panel);background:color-mix(in srgb,var(--sc) var(--pint),var(--bg));border-top:4px solid var(--sc);box-shadow:0 2px 10px rgba(0,0,0,.08),0 0 0 1px color-mix(in srgb,var(--sc) 25%,transparent)}
@media(prefers-color-scheme:dark){.board>section{box-shadow:0 2px 10px rgba(0,0,0,.35),0 0 0 1px color-mix(in srgb,var(--sc) 30%,transparent)}}
#si{--sc:var(--in)}#so{--sc:var(--outc)}
.board>section>h2{display:flex;align-items:center;gap:10px;margin:8px 2px;font-size:15px;letter-spacing:.08em;text-transform:uppercase;color:var(--sc)}
.board>section>h2::after{content:"";flex:1;height:2px;border-radius:1px;background:var(--sc);opacity:.4}
.board.one{grid-template-columns:repeat(var(--tot,1),minmax(0,1fr));column-gap:var(--gap)}
.board.one>section:first-child{grid-column:span var(--ci,1)}.board.one>section:last-child{grid-column:span var(--co,1)}
.row{display:grid;gap:8px;align-items:start;margin-bottom:8px}
.board:not(.one) .row{grid-template-columns:repeat(auto-fill,minmax(118px,1fr))}
.board.one #in{grid-template-columns:repeat(var(--ci,1),minmax(0,1fr))}
.board.one #out{grid-template-columns:repeat(var(--co,1),minmax(0,1fr))}
.st{container-type:inline-size;min-width:0;background:var(--panel);background:color-mix(in srgb,var(--sc) var(--tint),var(--panel));border-top:3px solid var(--sc);border-radius:12px;padding:10px 8px;display:flex;flex-direction:column;align-items:center;gap:8px;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none}
.st input{-webkit-user-select:text;user-select:text}
.nm{font-size:14px;font-weight:600;text-align:center;min-height:2.6em;overflow:hidden;word-break:break-word;width:100%;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
/* Hauteur des faders : calculée par la page (--fh) pour que tout tienne à l'écran */
.fd{display:flex;gap:8px;justify-content:center;height:var(--fh,240px)}
.mt{width:10px;background:var(--bg);border-radius:5px;overflow:hidden}
.mt i{display:block;height:100%;clip-path:inset(100% 0 0 0);transition:clip-path .12s;background:linear-gradient(0deg,var(--ok) 0 72%,var(--warn) 72% 90%,var(--mute) 90%)}
.tr{--th:30px;position:relative;width:46px;background:var(--bg);border-radius:23px;touch-action:none;cursor:pointer}
.tr::before{content:"";position:absolute;left:50%;top:calc(var(--th) / 2 - 1px);bottom:calc(var(--th) / 2 - 1px);width:4px;margin-left:-2px;background:var(--soft);opacity:.4;border-radius:2px}
.th{position:absolute;left:3px;right:3px;height:var(--th);border-radius:calc(var(--th) / 2);background:var(--sc);bottom:calc(var(--p) * (100% - var(--th)));box-shadow:0 1px 3px rgba(0,0,0,.18),inset 0 1px 0 rgba(255,255,255,.12);transition:background .12s}
.tr:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid var(--acc);outline-offset:2px}
.db{font-variant-numeric:tabular-nums;color:var(--soft);font-size:14px}
button{min-height:44px;padding:0 14px;border:0;border-radius:9px;font:inherit;font-weight:600;background:var(--bg);color:var(--ink);cursor:pointer;touch-action:manipulation;transition:background .12s,color .12s,opacity .12s,transform .08s}
button:active{transform:scale(.97)}button:disabled{opacity:.6;cursor:wait}
.st button{width:100%}button.m{background:var(--mute);color:#fff}
/* Bouton Couper plus gros que les autres (c'est le plus utilise) */
.st .mu{min-height:52px;font-size:15px;font-weight:700;letter-spacing:.02em;border-radius:10px}
body.compact .st .mu{min-height:40px;font-size:13px}
.pm{display:grid;grid-template-columns:1fr 1fr;gap:6px;width:100%}
.pm button{min-height:40px;padding:0;font-size:22px;line-height:1}
/* Boutons de l'en-tete : icone seule, carre arrondi uniforme (texte dans l'infobulle) */
.hb button{display:inline-flex;align-items:center;justify-content:center;width:44px;min-width:44px;padding:0;gap:0}
.hb button:hover{background:color-mix(in srgb,var(--acc) 18%,var(--bg))}
.ic{width:20px;height:20px;flex:none;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;pointer-events:none;transition:transform .3s}
.hb button.loading .ic{animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@media(pointer:coarse){.hb button{width:48px;min-width:48px}.ic{width:22px;height:22px}}
/* Reglages en MODAL (popup) avec fond assombri */
#backdrop{position:fixed;inset:0;background:rgba(10,15,20,.55);z-index:50;animation:fade .15s ease-out}
@keyframes fade{from{opacity:0}to{opacity:1}}
#set{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:51;background:var(--panel);border-radius:16px;padding:0;width:min(540px,calc(100vw - 24px));max-height:calc(100vh - 40px);display:flex;flex-direction:column;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.4);border:1px solid color-mix(in srgb,var(--ink) 10%,transparent);animation:pop .18s cubic-bezier(.2,.9,.3,1.1)}
@keyframes pop{from{opacity:0;transform:translate(-50%,-50%) scale(.95)}to{opacity:1;transform:translate(-50%,-50%) scale(1)}}
#set .head{display:flex;justify-content:space-between;align-items:center;padding:12px 16px;background:color-mix(in srgb,var(--acc) 8%,var(--panel));border-bottom:1px solid color-mix(in srgb,var(--ink) 10%,transparent)}
#set .head h3{margin:0;font-size:16px;font-weight:700}
#set .head button{min-height:36px;padding:0 12px;font-size:18px;background:transparent;line-height:1;border-radius:8px}
#set .head button:hover{background:var(--bg)}
#set .body{padding:16px;overflow-y:auto;display:flex;flex-direction:column;gap:6px}
#set label{font-size:13px;color:var(--soft);font-weight:600;margin-top:6px}
#set input,#set select{min-height:44px;padding:0 12px;border-radius:9px;border:2px solid var(--bg);background:var(--bg);color:var(--ink);font:inherit}
#set input:focus,#set select:focus{border-color:var(--acc);outline:none}
#set .acts{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px;padding-top:12px;border-top:1px solid color-mix(in srgb,var(--ink) 8%,transparent)}
#set .acts button{flex:1 1 auto;min-width:120px}
#set #sv{background:var(--acc);color:var(--panel)}
#set #rm{background:color-mix(in srgb,var(--mute) 15%,var(--panel));color:var(--mute)}
#set h3.sub{flex-basis:100%;margin:0;font-size:15px}
.fl{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;width:100%}.fb{min-height:36px;padding:0;font-size:13px}.fb.on{background:var(--sc);color:var(--bg)}
.nm{cursor:pointer}.kn{width:100%;display:grid;gap:6px}.kb{font-size:12px;color:var(--soft)}.kb input{width:100%;height:28px;margin:0;accent-color:var(--sc)}
details{width:100%}details[open]{display:grid;gap:6px}summary{cursor:pointer;font-size:13px;font-weight:600;min-height:32px;display:flex;align-items:center;touch-action:manipulation}
.eb{display:grid;gap:2px;font-size:12px;color:var(--soft);margin-bottom:6px;min-width:0}.eb input[type=range]{width:100%;height:24px;margin:0;accent-color:var(--sc)}
.hz{width:100%;min-height:28px;padding:0 6px;border:0;border-radius:6px;background:var(--bg);color:var(--ink);font:inherit;font-size:13px}
.z{min-height:28px!important;width:auto!important;padding:0 10px!important;font-size:12px;justify-self:start;flex:none}.kb>span,.eb>span{display:flex;justify-content:space-between;align-items:center;gap:4px}.db{cursor:pointer}
/* Téléphone : 2 voies par ligne */
@media(max-width:520px){h1{font-size:17px}.board:not(.one) .row{grid-template-columns:repeat(2,minmax(0,1fr))}}
/* Écran tactile : faders et curseurs plus gros */
@media(pointer:coarse){.tr{--th:38px;width:52px;border-radius:26px}.kb input,.eb input[type=range]{height:36px}.fb{min-height:42px}.hz{min-height:40px;font-size:16px}.z{min-height:36px!important}summary{min-height:44px}.pm button{min-height:46px}}
/* Paysage très bas (téléphone couché) */
@media(max-height:480px) and (orientation:landscape){header{position:static}}
/* Contenu d'une voie selon SA largeur (largeur utile de la voie, pas celle de l'écran) */
@container (max-width:110px){.st{padding:8px 6px;gap:6px}.pm button{min-height:34px;font-size:18px}.st button{min-height:36px;padding:0 6px;font-size:13px}summary{min-height:28px;font-size:12px}.nm{min-height:2.2em;font-size:13px}}
@container (max-width:99px){.fl{grid-template-columns:repeat(2,1fr)}.fb{font-size:12px}.kb>span,.eb>span{flex-wrap:wrap}.pm button{min-height:30px;font-size:16px}.st button{min-height:32px;font-size:12px}}
@container (max-width:84px){.fd{gap:6px}.tr{width:40px}.mt{width:8px}.nm{font-size:12px;min-height:2em;-webkit-line-clamp:2}.fb{font-size:11px;min-height:30px}.db{font-size:12px}.pm button{min-height:28px}.st button{min-height:30px}summary{min-height:26px;font-size:11px}}
/* Pendant le calcul de la hauteur des faders : réglages et panneaux ouverts ignorés */
.fitting #set{display:none!important}.fitting details[open]>:not(summary){display:none!important}
/* Mode compact global : forcé par la page quand même en 2 lignes ça déborderait (ex. Potato sur tablette) */
body.compact .st{padding:6px 6px;gap:5px}
body.compact .st>button{min-height:34px;font-size:13px}
body.compact .pm button{min-height:30px;font-size:17px}
body.compact summary{min-height:26px;font-size:12px}
body.compact .nm{min-height:2em;font-size:13px}
body.compact .fl{gap:3px}body.compact .fb{min-height:30px;font-size:11px}
body.compact .board>section>h2{margin:4px 2px;font-size:13px}
body.compact .board>section{padding:2px 8px 0}
body.compact header{padding:2px 0}body.compact h1{font-size:16px;margin:3px 0}
#d,#sm{color:var(--soft);font-size:13px;text-align:center}#err{color:var(--mute);text-align:center;min-height:1.2em}
</style></head><body><main>
<header><h1><img class="brand" src="?r=logo" alt="VM"onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'brand','aria-hidden':'true',textContent:this.alt}))">Console Voicemeeter</h1><div class="hb">
<button id="rc" title="Reconnecter" aria-label="Reconnecter"><svg class="ic" viewBox="0 0 24 24"><path d="M3 12a9 9 0 0115.5-6.3L21 8M21 12a9 9 0 01-15.5 6.3L3 16M21 3v5h-5M3 21v-5h5"/></svg></button>
<button id="fs" title="Plein écran" aria-label="Plein écran"><svg class="ic" viewBox="0 0 24 24"><path d="M4 10V4h6M20 10V4h-6M4 14v6h6M20 14v6h-6"/></svg></button>
<button id="g" title="Réglages" aria-label="Réglages"><svg class="ic" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09a1.65 1.65 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg></button>
</div></header>
<div id="backdrop" hidden></div>
<div id="set" hidden role="dialog" aria-modal="true" aria-labelledby="sh">
  <div class="head"><h3 id="sh"></h3><button id="sx" aria-label="Fermer" title="Fermer">✕</button></div>
  <div class="body">
    <label for="lb">Nom de ce PC (affiché dans l'onglet)</label>
    <input id="lb" maxlength="40" placeholder="PC 1 (par défaut)">
    <label for="ip">IP du PC Voicemeeter</label>
    <input id="ip" inputmode="decimal">
    <label for="st">Flux VBAN</label>
    <input id="st" maxlength="16">
    <label for="kd">Version de Voicemeeter</label>
    <select id="kd"><option value="basic">Basic</option><option value="banana">Banana</option><option value="potato">Potato</option></select>
    <div class="acts">
      <button id="sv">Enregistrer</button>
      <button id="add">+ Ajouter un PC</button>
      <button id="rm">Retirer ce PC</button>
    </div>
    <span id="sm"></span>
  </div>
</div>
<nav id="tabs" hidden></nav>
<div class="board" id="board"><section id="si"><h2>Entrées</h2><div class="row" id="in"></div></section><section id="so"><h2>Sorties</h2><div class="row" id="out"></div></section></div>
<div id="err" role="status"></div><div id="d"></div>
</main><script>
/* STEP = pas d'incrément des boutons - / + et des flèches du clavier (en dB). Mets 0.5 pour un réglage plus fin, 2 ou 3 pour plus rapide. */
const STEP=1;
let S={},order=[],last={},tm={},VM=0,sig="",tabs="",J=null,ed=0;try{VM=+localStorage.getItem("vm")||0;}catch(e){}
const L={mono:"Mono",solo:"Solo","eq.on":"EQ",mc:"M.C.",karaoke:"K"};const KN={basic:"Basic",banana:"Banana",potato:"Potato"};const KL={pan:["Gauche ↔ Droite",-100,100],eq1:["Graves",-12,12],eq2:["Médiums",-12,12],eq3:["Aigus",-12,12],comp:["Compresseur",0,10,.5],gate:["Gate",0,10,.5],audib:["Audibilité",0,10,.5],cx:["Écho (FX)",-100,100],cy:["Brillance",-100,100],py:["Avant ↔ Arrière",-100,100]};const $=id=>document.getElementById(id),R=g=>Math.round(g*2)/2;
function setG(k,g){last[k]=Date.now();S[k].gain=R(Math.max(-60,Math.min(12,g)));paint(k);clearTimeout(tm[k]);tm[k]=setTimeout(()=>send({target:k,gain:S[k].gain}),60);}
/* Boutons - / + : pas défini par la constante STEP ci-dessus, appui long = répétition */
function rep(btn,k,d){let t,iv;const go=()=>setG(k,R(S[k].gain)+d),stop=()=>{clearTimeout(t);clearInterval(iv);};
btn.onpointerdown=e=>{e.preventDefault();stop();go();t=setTimeout(()=>{iv=setInterval(go,90);},400);};
btn.onpointerup=btn.onpointerleave=btn.onpointercancel=stop;btn.oncontextmenu=e=>e.preventDefault();
btn.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();go();}};}
function paint(k){const r=$("r-"+k),s=S[k];r.querySelector(".tr").style.setProperty("--p",(s.gain+60)/72);
r.querySelector(".tr").setAttribute("aria-valuenow",s.gain);r.querySelector(".db").textContent=s.gain.toFixed(1)+" dB";
const b=r.querySelector(".mu");b.className=s.mute?"mu m":"mu";b.textContent=s.mute?"Rétablir":"Couper";r.querySelectorAll(".fb").forEach(f=>f.classList.toggle("on",!!s.f[f.dataset.p]));}
function build(){$("in").innerHTML=$("out").innerHTML="";for(const k of order){const r=document.createElement("div");r.className="st";r.id="r-"+k;
r.innerHTML='<div class="nm"></div><div class="fd"><div class="mt"><i></i></div><div class="tr" role="slider" tabindex="0" aria-valuemin="-60" aria-valuemax="12"><div class="th"></div></div></div><div class="db"></div><div class="pm"><button type="button" class="bm" aria-label="Baisser de '+STEP+' dB">−</button><button type="button" class="bp" aria-label="Monter de '+STEP+' dB">+</button></div><button class="mu">Couper</button><div class="fl"></div><div class="kn"></div>';
const tr=r.querySelector(".tr");r.querySelector(".nm").onclick=()=>{const n=prompt("Nom de la voie (vide = nom par défaut)",S[k].name);if(n!==null)send({target:k,name:n.trim()});};
rep(r.querySelector(".bm"),k,-STEP);rep(r.querySelector(".bp"),k,STEP);
const kd=document.createElement("details");kd.innerHTML="<summary>"+("eq1" in S[k].k?"Égaliseur et pan":"Réglages")+"</summary>";for(const q in S[k].k){const [t,mn,mx,st]=KL[q];const w=document.createElement("label");w.className="kb";w.innerHTML="<span><span>"+t+" <b>"+S[k].k[q]+"</b></span><button type=button class=z>0</button></span><input type=range min="+mn+" max="+mx+" step="+(st||1)+" value="+S[k].k[q]+">";const inp=w.querySelector("input"),bb=w.querySelector("b");inp.oninput=()=>{last[k]=Date.now();S[k].k[q]=+inp.value;bb.textContent=inp.value;clearTimeout(tm[k+q]);tm[k+q]=setTimeout(()=>send({target:k,knob:q,value:+inp.value}),60);};inp.ondblclick=w.querySelector(".z").onclick=()=>{inp.value=0;inp.oninput();};kd.appendChild(w);}if(kd.children.length>1)r.querySelector(".kn").appendChild(kd);
if(S[k].eq){const d=document.createElement("details");d.innerHTML="<summary>Égaliseur</summary><button type=button class=z>Tout à 0</button>";const sls=[];S[k].eq.forEach((e,b)=>{const w=document.createElement("div");w.className="eb";w.innerHTML="<span><span>Bande "+(b+1)+" <b>0</b> dB</span><button type=button class=z>0</button></span><input type=range min=-12 max=12 step=.5 value=0><input class=hz type=number min=20 max=20000 placeholder=Hz>";const sl=w.children[1],hz=w.children[2],bb=w.querySelector("b");sl.oninput=()=>{bb.textContent=sl.value;clearTimeout(tm[k+"e"+b]);tm[k+"e"+b]=setTimeout(()=>send({target:k,eqband:b,param:"g",value:+sl.value}),60);};sl.ondblclick=w.querySelector(".z").onclick=()=>{sl.value=0;sl.oninput();};sls.push(sl);hz.onchange=()=>{if(hz.value)send({target:k,eqband:b,param:"f",value:+hz.value});};d.appendChild(w);});d.querySelector(".z").onclick=()=>sls.forEach(s=>{s.value=0;s.oninput();});r.querySelector(".kn").appendChild(d);}
for(const p in S[k].f){const f=document.createElement("button");f.className="fb";f.dataset.p=p;f.textContent=L[p]||p;f.onclick=()=>{last[k]=Date.now();S[k].f[p]=!S[k].f[p];paint(k);send({target:k,flag:p});};r.querySelector(".fl").appendChild(f);}tr.setAttribute("aria-label","Volume "+S[k].name);
const mv=e=>{const b=tr.getBoundingClientRect(),h=tr.firstChild.offsetHeight;setG(k,-60+72*(1-Math.max(0,Math.min(1,(e.clientY-b.top-h/2)/(b.height-h)))));};
tr.onpointerdown=e=>{tr.setPointerCapture(e.pointerId);mv(e);};tr.onpointermove=e=>{if(tr.hasPointerCapture(e.pointerId))mv(e);};
tr.ondblclick=r.querySelector(".db").onclick=()=>setG(k,0);
tr.onkeydown=e=>{const d={ArrowUp:STEP,ArrowRight:STEP,ArrowDown:-STEP,ArrowLeft:-STEP}[e.key];if(d){e.preventDefault();setG(k,S[k].gain+d);}};
r.querySelector(".mu").onclick=()=>{last[k]=Date.now();S[k].mute=!S[k].mute;paint(k);send({target:k,toggle_mute:true});};
$(k[0]=="i"?"in":"out").appendChild(r);}
layout();}
async function send(b,u){if(!u)b.vm=VM;try{const r=await fetch(u||"?r=cmd",{method:"POST",body:JSON.stringify(b)});const j=await r.json();
if(!r.ok)throw new Error(j.error||r.status);$("err").textContent="";return j;}catch(e){$("err").textContent="Commande non envoyée : "+e.message;}}
function draw(d,ip){for(const k of order){const s=S[k],r=$("r-"+k);r.querySelector(".nm").textContent=s.name;r.querySelector(".nm").title=s.name;
r.querySelector("i").style.clipPath="inset("+(100-Math.max(0,Math.min(100,(s.level+60)/60*100)))+"% 0 0 0)";
if(Date.now()-(last[k]||0)>1500)paint(k);}
$("d").textContent=(J.label||("PC "+(VM+1)))+" · "+KN[J.kind]+" · "+ip+" | Niveaux : "+d.levels+" | Valeurs : "+d.values;}
function pick(i,force){if(i===VM&&!force)return;for(const t in tm)clearTimeout(tm[t]);VM=i;sig="";try{localStorage.setItem("vm",i);}catch(e){}tick();}
function drawTabs(){const n=$("tabs");n.innerHTML="";n.hidden=J.vms.length<2;J.vms.forEach((v,i)=>{const b=document.createElement("button");b.className="tab"+(i==VM?" on":"");const nm=v.label||("PC "+(i+1));b.innerHTML="<span></span> <small></small>";b.firstChild.textContent=nm;b.querySelector("small").textContent=KN[v.kind]+" · "+v.ip;b.onclick=()=>pick(i);n.appendChild(b);});}
function fillSet(){ed=VM;const nm=J.label||("PC "+(VM+1));$("sh").textContent="Réglages — "+nm;$("lb").value=J.label||"";$("lb").placeholder="PC "+(VM+1)+" (par défaut)";$("ip").value=J.ip;$("st").value=J.stream;$("kd").value=J.kind;$("rm").hidden=J.vms.length<2;$("add").hidden=J.vms.length>=J.max;$("sm").textContent="";}
let busy=false,again=false;async function tick(){if(busy){again=true;return;}busy=true;clearTimeout(tm.tick);const want=VM;
try{const j=await(await fetch("?r=state&vm="+want)).json();if(want===VM){J=j;VM=j.vm;const loc=S;S=j.ch;
const s=j.vm+"|"+j.kind+"|"+Object.keys(j.ch).join();
if(s!==sig){sig=s;last={};order=Object.keys(S);build();fillSet();}else for(const k in loc){if(Date.now()-(last[k]||0)<=1500)S[k]=loc[k];}
const t=JSON.stringify(j.vms)+VM;if(t!==tabs){tabs=t;drawTabs();relayout();}draw(j.diag,j.ip);}}
catch(e){$("err").textContent="Serveur injoignable";}busy=false;if(again){again=false;tick();}else tm.tick=setTimeout(tick,200);}
/* Reglages en MODAL : ouvre/ferme via hidden + backdrop, ferme aussi au clic hors / touche Echap */
function openSet(){if(J)fillSet();$("set").hidden=false;$("backdrop").hidden=false;setTimeout(()=>$("lb").focus(),50);}
function closeSet(){$("set").hidden=true;$("backdrop").hidden=true;}
$("g").onclick=()=>$("set").hidden?openSet():closeSet();
$("sx").onclick=closeSet;$("backdrop").onclick=closeSet;
addEventListener("keydown",e=>{if(e.key==="Escape"&&!$("set").hidden)closeSet();});
$("add").onclick=()=>{ed=J.vms.length;$("sh").textContent="Nouveau PC "+(ed+1);$("lb").value="";$("lb").placeholder="PC "+(ed+1)+" (par défaut)";$("ip").value="";$("st").value="Command1";$("kd").value="banana";$("rm").hidden=$("add").hidden=true;$("sm").textContent="";$("ip").focus();};
$("sv").onclick=async()=>{$("sm").textContent="Connexion...";const j=await send({vm:ed,ip:$("ip").value.trim(),stream:$("st").value.trim(),kind:$("kd").value,label:$("lb").value.trim()},"?r=config");$("sm").textContent=j?"Enregistré : "+(j.label||("PC "+(j.vm+1)))+" ("+j.ip+")":"Échec";if(j){pick(j.vm,true);setTimeout(closeSet,600);}};
$("rm").onclick=async()=>{if(!confirm("Retirer le PC "+(VM+1)+" de la console ?"))return;const j=await send({vm:VM,remove:true},"?r=config");if(j){closeSet();pick(0,true);}};
/* Changement de nom instantane (sans reconnexion) quand on quitte le champ ou qu'on appuie Entree */
let lbt;$("lb").oninput=()=>{clearTimeout(lbt);lbt=setTimeout(async()=>{if(ed===VM&&J)await send({vm:VM,rename:true,label:$("lb").value.trim()},"?r=config");},700);};
$("lb").onkeydown=e=>{if(e.key==="Enter"){e.preventDefault();$("sv").click();}};
/* Reconnecter : refait la connexion VBAN du PC affiché, sans passer par les réglages.
   On garde l'icône SVG et on la fait tourner le temps de la requête (classe .loading). */
$("rc").onclick=async()=>{const b=$("rc");b.disabled=true;b.classList.add("loading");b.title="Reconnexion…";await send({vm:VM,reconnect:true},"?r=config");b.classList.remove("loading");b.disabled=false;b.title="Reconnecter";};
/* Mise en page : toutes les voies sur une ligne si la largeur le permet, hauteur des faders calculée pour tenir à l'écran.
   Si même au mini on déborde (p.ex. Potato en 2 lignes sur tablette), on bascule en mode compact global. */
const MINW=74,FMIN=90,FMAX=520;
function fitOnce(){const r=document.documentElement,b=document.body;
const at=v=>{r.style.setProperty("--fh",v+"px");return b.offsetHeight;};const h1=at(200),h2=at(300),sl=(h2-h1)/100;
let fh=sl>.5?200+(innerHeight-h1)/sl:240;return Math.round(Math.max(FMIN,Math.min(FMAX,fh)));}
function fit(){const r=document.documentElement,b=document.body;if(!b.querySelector(".fd"))return;
b.classList.remove("compact");r.classList.add("fitting");
let fh=fitOnce();
/* Si on est resté coincé contre le mini et qu'on déborde encore, on active le compact et on refait le calcul */
if(fh<=FMIN+2){r.style.setProperty("--fh",FMIN+"px");if(b.offsetHeight>innerHeight+2){b.classList.add("compact");fh=fitOnce();}}
r.style.setProperty("--fh",fh+"px");r.classList.remove("fitting");}
function layout(){const bd=$("board"),n=order.length;if(!n)return;const ni=order.filter(k=>k[0]=="i").length;
bd.style.setProperty("--ci",ni);bd.style.setProperty("--co",n-ni);bd.style.setProperty("--tot",n);
/* Pour la bascule 1-ligne / 2-lignes, on compte les ecarts entre voies (8 px) + l'ecart central entre entrees/sorties (--gap, lu dans le CSS) */
const g=parseInt(getComputedStyle(bd).getPropertyValue("--gap"))||28,w=document.querySelector("main").clientWidth-(n-1)*8-g-24;
bd.classList.toggle("one",w/n>=MINW);fit();}
let rt;function relayout(){clearTimeout(rt);rt=setTimeout(layout,120);}
addEventListener("resize",relayout);addEventListener("orientationchange",relayout);
/* Plein écran (masqué si le navigateur ne le permet pas, ex. iPhone). On garde l'icône et on la swap entre "agrandir" / "réduire". */
const FE=document.documentElement,fsOn=()=>document.fullscreenElement||document.webkitFullscreenElement;
const ICO_EXPAND='<path d="M4 10V4h6M20 10V4h-6M4 14v6h6M20 14v6h-6"/>',ICO_SHRINK='<path d="M10 4v6H4M14 4v6h6M10 20v-6H4M14 20v-6h6"/>';
function fsLabel(){const b=$("fs"),on=fsOn();b.title=on?"Quitter le plein écran":"Plein écran";b.setAttribute("aria-label",b.title);b.querySelector(".ic").innerHTML=on?ICO_SHRINK:ICO_EXPAND;}
function fsChange(){fsLabel();relayout();setTimeout(layout,400);}
if(!(FE.requestFullscreen||FE.webkitRequestFullscreen))$("fs").hidden=true;
$("fs").onclick=()=>{try{if(fsOn())(document.exitFullscreen||document.webkitExitFullscreen).call(document);else(FE.requestFullscreen||FE.webkitRequestFullscreen).call(FE);}catch(e){}};
document.addEventListener("fullscreenchange",fsChange);document.addEventListener("webkitfullscreenchange",fsChange);
tick();</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_asset(self, path, fallback_svg_body):
        """Envoie un fichier (logo/favicon) depuis le dossier du script, ou un SVG de secours."""
        if path:
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception:
                pass
        self._send(200, fallback_svg_body, "image/svg+xml")

    def do_GET(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        r = q.get("r", [None])[0]
        if u.path == "/state" or r == "state":
            try:
                n = int(q.get("vm", ["0"])[0])
            except ValueError:
                n = 0
            with lock:
                n = n if 0 <= n < len(vms) else 0
                v = vms[n]
                body = json.dumps({"ch": v.state, "diag": v.diag, "ip": v.ip, "stream": v.stream, "kind": v.kind,
                                   "label": v.label, "vm": n, "max": MAX_VM,
                                   "vms": [{"ip": x.ip, "kind": x.kind, "label": x.label} for x in vms]})
            self._send(200, body)
        elif r == "logo":
            # Logo d'en-tete : logo.svg / logo.png / logo.jpg / logo.webp dans le dossier du script, sinon badge texte par defaut
            self._send_asset(asset_path(LOGO_NAMES), fallback_svg(BRAND_NAME, 220, 60, 28))
        elif r == "favicon" or u.path == "/favicon.ico":
            # Favicon : favicon.svg / favicon.ico / favicon.png dans le dossier du script, sinon pastille BRAND_SHORT
            self._send_asset(asset_path(FAVICON_NAMES), fallback_svg(BRAND_SHORT, 32, 32, 18))
        else:
            self._send(200, PAGE, "text/html; charset=utf-8")

    def config(self, req):
        n = int(req.get("vm", 0))
        if req.get("remove"):
            if len(vms) < 2 or not 0 <= n < len(vms):
                raise ValueError("il faut garder au moins un PC")
            vms.pop(n).close()
            save_conf()
            return {"ok": True, "vm": 0}
        if req.get("reconnect"):  # bouton Reconnecter : refait seulement la connexion VBAN
            if not 0 <= n < len(vms):
                raise ValueError("PC inconnu")
            vms[n].connect()
            return {"ip": vms[n].ip, "vm": n}
        if req.get("rename") is True:  # changement de nom sans reconnexion (leger)
            if not 0 <= n < len(vms):
                raise ValueError("PC inconnu")
            vms[n].label = str(req.get("label") or "").strip()[:40]
            save_conf()
            return {"label": vms[n].label, "vm": n}
        ip = str(ipaddress.ip_address(req.get("ip", "")))
        stream = str(req.get("stream") or STREAM).strip()[:16]
        kind = req.get("kind", "banana")
        label = str(req.get("label", "") or "").strip()[:40]
        if kind not in LAYOUT:
            raise ValueError("version inconnue")
        if 0 <= n < len(vms):
            old = vms[n]
        elif n == len(vms) < MAX_VM:
            old = None
        else:
            raise ValueError(f"{MAX_VM} PC maximum")
        # Si l'utilisateur n'a pas tape de nom et qu'un ancien PC en avait un, on le conserve
        if not label and old:
            label = old.label
        v = VM(ip, stream, kind, old.names if old and old.kind == kind else {}, label)
        if old:
            old.close()
        try:
            v.connect()
        except Exception:
            if old:
                try:
                    old.connect()
                except Exception:
                    pass
            raise
        if old:
            vms[n] = v
        else:
            vms.append(v)
        save_conf()
        return {"ip": ip, "vm": n}

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        u = urlsplit(self.path)
        if u.path == "/config" or parse_qs(u.query).get("r") == ["config"]:
            try:
                with lock:
                    res = self.config(req)
            except Exception as exc:
                return self._send(400, json.dumps({"error": f"Réglage refusé ({exc})"}))
            return self._send(200, json.dumps(res))
        with lock:
            try:
                v = vms[int(req.get("vm", 0))]
            except (ValueError, IndexError):
                return self._send(400, json.dumps({"error": "PC inconnu"}))
            k = req.get("target")
            if k not in v.state:
                return self._send(400, json.dumps({"error": "cible inconnue"}))
            st = v.state[k]
            try:
                if "gain" in req:
                    g = max(-60.0, min(12.0, float(req["gain"])))
                    v.zone(k).gain = g
                    st["gain"] = g
                if "knob" in req and req["knob"] in st["k"]:
                    q = req["knob"]
                    pname, scale, lo, hi = KNOBS[q]
                    val = max(lo, min(hi, float(req["value"])))
                    v.send_param(k, pname, round(val * scale, 3))
                    st["k"][q] = val
                if "eqband" in req and "eq" in st and req.get("param") in EQ_PARAM:
                    b = int(req["eqband"])
                    if not 0 <= b < EQ_BANDS:
                        raise ValueError("bande inconnue")
                    pn, lo, hi = EQ_PARAM[req["param"]]
                    val = max(lo, min(hi, float(req["value"])))
                    for ch in (0, 1):  # canaux gauche et droite
                        v.send_param(k, f"EQ.channel[{ch}].cell[{b}].{pn}", round(val, 2))
                    st["eq"][b][req["param"]] = val
                if "name" in req:
                    # sans " ni ; : le nom est renvoyé tel quel dans une commande VBAN-TEXT
                    nm = str(req["name"]).replace('"', "").replace(";", "").strip()[:30]
                    if nm:
                        v.names[k] = nm
                    else:
                        v.names.pop(k, None)
                    st["name"] = v.names.get(k) or v.base[k]
                    save_conf()
                    try:  # affiche aussi le nom dans Voicemeeter
                        v.send_param(k, "Label", f'"{nm}"')
                    except Exception:
                        pass
                if "flag" in req and req["flag"] in st["f"]:
                    p = req["flag"]
                    new = not st["f"][p]
                    flag_set(v.zone(k), p, new)
                    st["f"][p] = new
                if req.get("toggle_mute"):
                    new = not st["mute"]
                    v.zone(k).mute = new
                    st["mute"] = new
            except Exception as exc:
                return self._send(500, json.dumps({"error": str(exc)}))
        self._send(200, json.dumps({"ok": True}))

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    try:
        with open(CONF, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        cfg = {}
    # ancien format {"ip", "names"} : repris comme un seul PC Banana
    for c in (cfg.get("vms") or [{"ip": cfg.get("ip", DEFAULT_IP), "names": cfg.get("names", {})}])[:MAX_VM]:
        v = VM(c.get("ip", DEFAULT_IP), c.get("stream") or STREAM,
               c.get("kind") if c.get("kind") in LAYOUT else "banana",
               c.get("names"), c.get("label", ""))
        try:
            v.connect()
        except Exception as exc:
            v.diag["values"] = f"connexion impossible ({exc})"
        vms.append(v)
    threading.Thread(target=poll, daemon=True).start()
    for i, v in enumerate(vms):
        print(f"PC {i + 1} : Voicemeeter {v.kind.capitalize()} {v.ip} (flux {v.stream})")
    print(f"Console sur http://0.0.0.0:{WEB_PORT}")
    ThreadingHTTPServer(("0.0.0.0", WEB_PORT), Handler).serve_forever()