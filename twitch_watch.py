"""
Surveillance des CLIPS Twitch (alerte groupée).

À chaque exécution :
  - lit config/twitch_channels.json (liste de streamers)
  - pour chaque streamer, récupère via yt-dlp les clips récents (dernières 24 h)
  - repère les clips jamais vus (comparaison avec data/twitch_watch_state.json)
  - s'il y a du nouveau, envoie UNE seule notif groupée : combien de nouveaux
    clips par streamer, avec un rappel de lancer run_twitch.bat.

Un streamer ajouté pour la première fois enregistre ses clips actuels SANS
notifier (sinon tu recevrais un paquet d'alertes d'un coup).

Usage :
    python twitch_watch.py          # vérifie et notifie (groupé)
    python twitch_watch.py --test   # notif de test
    python twitch_watch.py --reset  # ré-enregistre l'état actuel comme référence

Tourne dans le cloud (GitHub Actions), voir .github/workflows/twitch_watch.yml.
Nécessite yt-dlp (installé dans le workflow ; en local, mets-le sur le PATH ou
défini YTDLP_BIN).
"""
import os
import sys
import json
import time
import subprocess
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"), override=True)
except Exception:
    pass

CHANNELS_PATH = os.path.join(ROOT, "config", "twitch_channels.json")
STATE_PATH = os.path.join(ROOT, "data", "twitch_watch_state.json")

RANGE = "24hr"     # fenêtre de clips récents interrogée
LIMIT = 40         # nb max de clips récupérés par streamer et par passage
CAP = 300          # nb max d'identifiants mémorisés par streamer


def _ytdlp() -> str:
    return os.environ.get("YTDLP_BIN", "yt-dlp")


def send_ntfy(title: str, message: str) -> bool:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC vide : notification non envoyée.")
        return False
    payload = {"topic": topic, "title": title, "message": message, "tags": ["clapper"]}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://ntfy.sh/", data=data, headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        print(f"Echec envoi ntfy : {e}")
        return False


def load_channels() -> list:
    with open(CHANNELS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state() -> dict:
    # Tolérant : un fichier absent, vide ou corrompu repart d'un état vide
    # (mieux vaut ré-enregistrer que planter la surveillance).
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_recent_clip_ids(user: str) -> list:
    """Identifiants des clips récents du streamer (ordre yt-dlp), ou []."""
    url = f"https://www.twitch.tv/{user}/clips?range={RANGE}"
    cmd = [_ytdlp(), "--flat-playlist", "--dump-json", "--no-warnings",
           "-I", f"1:{LIMIT}", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", timeout=120)
    except Exception as e:
        print(f"  [{user}] erreur yt-dlp : {e}")
        return []
    ids = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("id"):
            ids.append(str(e["id"]))
    return ids


def _dedup(seq: list) -> list:
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def main():
    if "--test" in sys.argv:
        ok = send_ntfy(
            "🎬 Surveillance Twitch ✅",
            "Test : tu recevras ici un résumé quand de nouveaux clips sortent "
            "sur les streamers surveillés.",
        )
        print("Notif de test envoyée." if ok else "Echec (voir ci-dessus).")
        return

    channels = load_channels()
    state = load_state()
    reset = "--reset" in sys.argv

    digest = []  # (nom, nb_nouveaux)
    for ch in channels:
        user = ch["user"]
        name = ch.get("name", user)
        current = fetch_recent_clip_ids(user)
        known = state.get(user)

        # Streamer jamais vu (ou reset) : on enregistre une référence SANS
        # notifier — même si `current` est vide (streamer inactif). Ainsi, quand
        # il streamera, ses clips seront vus comme NOUVEAUX (et donc notifiés),
        # au lieu d'être avalés silencieusement comme un "premier passage".
        if known is None or reset:
            state[user] = current[:CAP]
            time.sleep(0.3)
            continue

        # Déjà connu mais rien de récent ce coup-ci : on garde l'état tel quel.
        if not current:
            time.sleep(0.3)
            continue

        seen = set(known)
        new = [cid for cid in current if cid not in seen]
        if new:
            digest.append((name, len(new)))
        # Mémorise les clips vus (nouveaux + anciens), borné à CAP.
        state[user] = _dedup(current + known)[:CAP]
        time.sleep(0.3)

    save_state(state)

    if digest:
        total = sum(n for _, n in digest)
        lignes = "\n".join(f"• {nom} : {n} nouveau(x) clip(s)" for nom, n in digest)
        send_ntfy(
            f"🎬 {total} nouveau(x) clip(s) Twitch",
            f"{lignes}\n\nLance run_twitch.bat pour en faire des Shorts.",
        )
        print(f"Notif groupée envoyée ({total} nouveaux clips).")
    else:
        print("Aucun nouveau clip." if state else "État initialisé.")


if __name__ == "__main__":
    main()
