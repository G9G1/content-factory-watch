"""
Rappels de publication dans le cloud (GitHub Actions).

Une fois par heure, de 07:00 à 23:00 (heure de Paris), envoie une notif ntfy
avec le prochain clip à poster : titre + légende + hashtags prêts à copier-
coller. Tu publies MANUELLEMENT depuis ton iPhone le clip correspondant de ta
pellicule (Photos), sur YouTube Short + Instagram + TikTok.

Fiabilité : le workflow se réveille souvent (toutes les ~15 min) mais on
n'envoie qu'UN rappel par heure d'horloge (repère `last_slot_key`). Ainsi,
même si GitHub saute ou retarde un réveil, le rappel de l'heure part quand même
dès le prochain réveil de la même heure (logique « auto-rattrapante »).

Tourne dans le cloud (indépendant du PC de Gaëtan, souvent éteint). La file
d'attente (data/publish_queue.json) est alimentée depuis le PC par
« planner.py --push ». Les clips vidéo eux-mêmes ne montent PAS ici : ils sont
déjà dans la pellicule iPhone via iCloud.

Usage :
    python post.py            # si l'heure n'a pas encore eu son rappel, l'envoie
    python post.py --force    # notifie tout de suite (ignore l'heure), pour tester
    python post.py --test     # simple notif de test
"""
import os
import sys
import json
import time
import urllib.request
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    PARIS = ZoneInfo("Europe/Paris")
except Exception:
    PARIS = None

ROOT = os.path.dirname(os.path.abspath(__file__))
QUEUE_PATH = os.path.join(ROOT, "data", "publish_queue.json")

PLATFORMS = ["YouTube Short", "Instagram", "TikTok"]
# Créneaux (heure de Paris) : toutes les heures de 7 h à 23 h inclus -> 17/jour.
SLOT_HOURS = set(range(7, 24))  # {7, 8, ..., 23}


def now_paris() -> datetime:
    return datetime.now(PARIS) if PARIS else datetime.now()


def send_ntfy(title: str, message: str) -> bool:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC vide : notification non envoyée.")
        return False
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "tags": ["calendar"],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://ntfy.sh/", data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        print(f"Echec envoi ntfy : {e}")
        return False


def load_queue() -> dict:
    if os.path.exists(QUEUE_PATH):
        with open(QUEUE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"items": [], "last_post_ts": 0}


def save_queue(q: dict) -> None:
    os.makedirs(os.path.dirname(QUEUE_PATH), exist_ok=True)
    with open(QUEUE_PATH, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=2)


def _caption(item: dict) -> str:
    tags = " ".join(item.get("hashtags", []))
    parts = [item.get("description", "").strip()]
    if item.get("cta"):
        parts.append(item["cta"].strip())
    if tags:
        parts.append(tags)
    return "\n\n".join(p for p in parts if p)


def _pick_next(items: list) -> dict:
    """
    Choisit le prochain clip à poster en ALTERNANT les sources (tourniquet).

    On envoie le 1er clip de chaque source, puis le 2e de chaque source, etc.
    -> la chaîne poste varié (vidéo A, vidéo B, Twitch, vidéo A...) au lieu de
    dérouler les 10 clips d'une même vidéo à la suite.

    Méthode : pour chaque clip on calcule son rang dans SA source (sur toute la
    file, clips déjà postés inclus, pour que l'alternance reste stable au fil
    des envois), puis parmi les clips restants on prend le plus petit rang ; à
    rang égal, on suit l'ordre d'apparition des sources.
    """
    counts, abs_rank = {}, []
    source_order = {}
    for it in items:
        s = it.get("source", "")
        abs_rank.append(counts.get(s, 0))
        counts[s] = counts.get(s, 0) + 1
        if s not in source_order:
            source_order[s] = len(source_order)

    best, best_key = None, None
    for idx, it in enumerate(items):
        if it.get("posted_at") is not None:
            continue
        key = (abs_rank[idx], source_order.get(it.get("source", ""), 0))
        if best_key is None or key < best_key:
            best, best_key = it, key
    return best


def post_next(force: bool = False) -> None:
    q = load_queue()
    t = now_paris()

    # Clé unique de l'heure en cours (ex. "2026-09-20-14"). On n'envoie qu'un
    # seul rappel par heure d'horloge : si cette heure a déjà eu le sien, on
    # saute ; sinon on l'envoie, même si GitHub s'est réveillé en retard.
    hour_key = t.strftime("%Y-%m-%d-%H")

    if not force:
        if t.hour not in SLOT_HOURS:
            print(f"Hors plage (il est {t.strftime('%H:%M')} à Paris). Rien à envoyer.")
            return
        if q.get("last_slot_key") == hour_key:
            print(f"Rappel déjà envoyé pour l'heure {t.strftime('%H')} h. On saute.")
            return

    nxt = _pick_next(q["items"])
    if nxt is None:
        print("File d'attente vide : plus de clips à publier.")
        # On marque quand même l'heure comme traitée pour ne pas re-scruter en
        # boucle une file vide à chaque réveil de la même heure.
        if not force:
            q["last_slot_key"] = hour_key
            save_queue(q)
        return

    slot = t.strftime("%H:%M")
    source = nxt.get("source", "")
    dossier = f"📁 Dossier Fichiers : {source}\n" if source else ""
    body = (
        f"🎬 {nxt['title']}\n"
        f"{dossier}"
        f"📲 Ouvre l'app Fichiers, enregistre le clip dans Photos, puis poste "
        f"sur : {' + '.join(PLATFORMS)}\n\n"
        f"— Légende à copier —\n{_caption(nxt)}"
    )
    ok = send_ntfy(f"📅 {slot} — C'est l'heure de poster !", body)

    nxt["posted_at"] = t.strftime("%Y-%m-%d %H:%M:%S")
    nxt["slot"] = slot
    q["last_slot_key"] = hour_key
    save_queue(q)
    pending = sum(1 for it in q["items"] if it.get("posted_at") is None)
    print(f"{'Notif envoyée' if ok else 'Echec notif'} — « {nxt['title']} » "
          f"({slot}). Restant : {pending}.")


def main():
    if "--test" in sys.argv:
        ok = send_ntfy(
            "📅 Rappels de publication ✅",
            "Test : à chaque créneau (8h→22h, heure de Paris) tu recevras ici "
            "le clip à poster depuis ta pellicule.",
        )
        print("Notif de test envoyée." if ok else "Echec (voir ci-dessus).")
        return
    post_next(force="--force" in sys.argv)


if __name__ == "__main__":
    main()
