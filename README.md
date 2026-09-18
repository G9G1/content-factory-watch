# Surveillance YouTube (cloud)

Vérifie toutes les 15 min les flux RSS des chaînes de `config/channels.json`
et envoie une notification ntfy sur le téléphone dès qu'une nouvelle vidéo sort.
Tourne gratuitement sur GitHub Actions — aucun PC allumé nécessaire.

## Réglages
- Secret **`NTFY_TOPIC`** (Settings → Secrets and variables → Actions) : le nom
  du canal ntfy sur lequel recevoir les alertes.
- `config/channels.json` : la liste des chaînes surveillées.
- `data/watch_state.json` : mémorise la dernière vidéo vue de chaque chaîne
  (mis à jour automatiquement par le workflow).

Le premier run enregistre l'état actuel sans notifier ; ensuite, seules les
vraies nouveautés déclenchent une alerte.
