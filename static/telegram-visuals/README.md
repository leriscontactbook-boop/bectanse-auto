# Bectanse Visual Catalog V3

Système visuel Telegram au format 1200 × 600 px, conçu pour conserver une
identité trading forte et un logo Bectanse constant sur chaque publication.

La V2 ajoute des boîtes dimensionnées selon leur contenu, le drapeau du
Royaume-Uni pour Londres, le drapeau américain pour New York et des détails
graphiques propres à chaque catégorie de publication.

La V3 ajoute un vrai catalogue filtrable dans l’admin, la conservation des
visuels personnels sur Cloudinary et trois directions de conversion sans
fausse urgence ni rareté artificielle.

## Règles de publication

- Utiliser le fichier WebP pour Telegram et conserver le PNG comme master.
- Conserver la légende sous 1 024 caractères lorsqu’une image est jointe.
- Ajouter le vrai bouton CTA Telegram sous la publication ; ne jamais dessiner
  un faux bouton dans le visuel.
- Limiter les émojis Premium à un dans le titre et un éventuel émoji de bouton.
- Conserver le fuseau `Europe/Paris` pour toutes les heures affichées.
- Ne pas ajouter automatiquement d’avertissement sur le risque sous chaque post.

## Modèles

1. Session Londres ouverte — bleu marché.
2. Session américaine dans 30 minutes — orange Bectanse.
3. Annonce économique majeure — ambre haute importance.
4. Résultat de la journée — vert bilan.
5. Quiz du marché — violet interactif.
6. Nouveau témoignage — or communauté.
7. Dernière alerte disponible — rouge signal.
8. CTA système — orange décision et processus.
9. CTA méthode répétable — violet progression.
10. CTA accompagnement — vert équipe et structure.

L’admin permet aussi d’ajouter un visuel personnel avec son nom, sa catégorie,
sa légende et son bouton CTA par défaut. Ces médias restent disponibles dans
le filtre « Mes visuels » et peuvent être réutilisés sur les futurs posts.

Le quiz natif Telegram ne peut pas recevoir une image dans le même message.
La bannière Quiz sert donc uniquement de teaser éventuel ; le quiz reste une
publication Telegram native et cliquable.

## Régénération

Le script `scripts/render_telegram_visual_templates.mjs` produit les PNG et
WebP, la planche de contrôle V3 et le manifeste JSON à partir du master visuel.
Les exports précédents sont conservés pour permettre la comparaison.
