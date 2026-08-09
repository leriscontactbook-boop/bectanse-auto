# Automatisation Telegram — Bectanse Académie

## Rythme de publication

- 08:30, heure de Paris : calendrier économique du jour, généré à partir des
  données vérifiées disponibles au moment de l'envoi. Si la source est
  indisponible, le robot n'envoie rien et prévient l'administrateur.
- Heure paramétrable depuis le centre admin : publication éditoriale du lundi
  au dimanche.

Le calendrier éditorial contient initialement 28 publications uniques réparties
sur quatre semaines. Elles sont importées automatiquement dans le centre de
commande lors de sa première mise en route.

## Centre de commande admin

La page `/admin/telegram-automation` permet de :

- enregistrer tous les canaux Telegram pilotés par le robot, tester ses droits
  de publication et activer ou suspendre chaque destination ;
- créer, modifier, mettre en pause et supprimer une publication ;
- envoyer chaque publication vers tous les canaux actifs ou seulement vers
  une sélection de canaux ;
- choisir un rythme hebdomadaire, une rotation de quatre semaines ou une date
  unique ;
- régler les jours, l'heure et le mode silencieux ;
- joindre une image stockée sur Cloudinary ;
- ajouter un bouton avec un lien ;
- créer un vrai quiz Telegram avec 2 à 12 réponses, une ou plusieurs bonnes
  réponses et une explication ;
- créer un sondage Telegram natif, anonyme ou non, avec choix unique ou
  multiple ;
- prévisualiser le rendu Telegram ;
- envoyer immédiatement un post enregistré ;
- consulter l'historique des réussites et des échecs.

## Planning CSV

La section « Importer une semaine par CSV » accepte un fichier UTF-8 séparé
par des points-virgules ou des virgules. Le modèle téléchargeable contient sept
exemples : messages, photo, quiz et sondage.

Chaque ligne peut définir :

- le format (`message`, `quiz` ou `sondage`) ;
- une date et une heure, ou un rythme hebdomadaire / rotation de 4 semaines ;
- le mode silencieux et l'état actif / brouillon ;
- `tous_les_canaux=oui` pour diffuser vers tous les canaux actifs, ou
  `tous_les_canaux=non` avec les identifiants Telegram séparés par `|` dans
  la colonne `canaux` pour cibler uniquement certains canaux déjà enregistrés ;
- un texte, une image HTTPS et un bouton ;
- une question, 2 à 12 réponses, les numéros des bonnes réponses et
  l'explication du quiz.

Le serveur contrôle l'intégralité du fichier avant l'import. Si une ligne est
incorrecte, aucune publication n'est créée. Réimporter le même contenu ne crée
pas de doublon.

## Sécurité éditoriale

Les publications sont éducatives. Elles ne contiennent pas de signal d'achat ou
de vente, de rendement garanti ni de conseil personnalisé. Chaque publication
porte l'avertissement sur le risque de perte et les performances passées.

## Anti-doublon

L'application utilise la table PostgreSQL `scheduled_publications`. Un créneau
unique est réservé pour chaque publication et chaque canal, ce qui empêche les
deux workers Gunicorn de publier le même message deux fois. Si un canal échoue,
les autres continuent à recevoir leur publication. Un envoi Telegram échoué est
tenté trois fois et peut être relancé ultérieurement.

## Ajouter un nouveau canal

1. Ajouter le robot Telegram comme administrateur du canal et lui donner le
   droit de publier.
2. Dans « Canaux connectés », saisir le nom du canal et son identifiant
   Telegram (`@nom_du_canal` ou identifiant numérique).
3. Enregistrer puis lancer « Tester » pour confirmer les autorisations.
4. Laisser le canal actif. Toutes les publications marquées « tous les canaux »
   y seront alors automatiquement dupliquées, y compris l'agenda économique.

## Variables nécessaires en production

- `ECO_BOT_TOKEN` : jeton du bot administrateur du canal public.
- `ECO_CANAL` : identifiant du canal, par défaut `@BECTANSE_ACADEMIE`.
- `DATABASE_URL` : base PostgreSQL utilisée pour l'anti-doublon.

Le service doit rester en ligne pour que le planificateur s'exécute. Le bot doit
avoir le droit de publier dans le canal.

## Vérification locale

```sh
BECTANSE_SKIP_STARTUP=1 python -m unittest tests/test_telegram_editorial.py
```
