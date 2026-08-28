"""Automatisation marketing Bectanse Académie.

Le moteur reste volontairement piloté par la base de l'application : Brevo assure
la délivrabilité, tandis que les segments, priorités, arrêts et preuves d'envoi
restent auditables dans l'administration Bectanse.
"""

import csv
import hashlib
import hmac
import html
import io
import json
import os
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from flask import jsonify, redirect, render_template, request, url_for


PARIS_TZ = ZoneInfo("Europe/Paris")
UTC_TZ = ZoneInfo("UTC")
BASE_URL = "https://acces.bectanse-academie.com"
SUPPORT_URL = "https://t.me/m/PAt88QgeZDhk"
MEMBER_ONBOARDING_START = datetime(2026, 8, 28)


EXPLORER_STAGES = [
    {
        "stage": "jour-1", "delay_hours": 20,
        "subject": "{prenom}, pourquoi Bectanse existe vraiment",
        "preheader": "L’histoire d’un système créé pour ne plus avancer seul face aux marchés",
        "eyebrow": "LE DÉCLIC",
        "title": "L’espace que Leris aurait aimé avoir à ses débuts",
        "body": [
            "Quand Leris a découvert les marchés, il n’avait ni parcours clair, ni outils réunis, ni équipe pour répondre à ses questions. Il a appris seul, avec ses erreurs et l’envie de comprendre",
            "Bectanse Académie est née de ce manque. L’objectif n’est pas de te donner une nouvelle source d’informations, mais un environnement capable de te guider, de centraliser les bons outils et de t’éviter d’avancer seul",
        ],
        "highlight": "Ton accès Explorer te permet d’observer ce système avant de décider s’il correspond à ce que tu veux construire",
        "cta": "Découvrir l’histoire de Bectanse",
        "target_url": BASE_URL + "/vip#histoire",
        "hero_image": BASE_URL + "/static/vip/assets/founder-leris.webp",
        "hero_alt": "Leris Luketo, fondateur de Bectanse Académie",
    },
    {
        "stage": "jour-3", "delay_hours": 68,
        "subject": "Voici comment tout fonctionne ensemble",
        "preheader": "Formation, application, outils et communauté suivent un seul parcours",
        "eyebrow": "COMPRENDRE LE SYSTÈME",
        "title": "Ce n’est pas une formation posée à côté de quelques outils",
        "body": [
            "Une information isolée change rarement une façon de travailler. Il faut savoir quoi apprendre, comment préparer une décision, comment gérer le risque et comment mesurer ce qui a été appliqué",
            "Bectanse réunit ce parcours dans la même application. La formation apporte les fondations, le Trader Lab structure la pratique, le Canal VIP relie la communauté et Bectanse Progress indique la prochaine étape",
        ],
        "highlight": "Chaque brique répond à un moment précis : comprendre, préparer, exécuter puis mesurer",
        "cta": "Voir l’écosystème complet",
        "target_url": BASE_URL + "/vip#formation",
        "hero_image": BASE_URL + "/static/vip/assets/bectanse-auto-robot-phone-v2.png",
        "hero_alt": "Application membre Bectanse Académie",
        "proof_items": [
            ("APPRENDRE", "Formation structurée"),
            ("PRATIQUER", "Trader Lab"),
            ("RESTER RELIÉ", "Canal VIP"),
        ],
    },
    {
        "stage": "jour-5", "delay_hours": 116,
        "subject": "À quoi servent vraiment les outils du Trader Lab",
        "preheader": "Analyse IA, simulateur, journal, calculateur et Trade Score ont chacun un rôle précis",
        "eyebrow": "LES OUTILS",
        "title": "Transformer une idée en processus observable",
        "body": [
            "Le Trader Lab ne décide pas à ta place. Il t’aide à préparer un scénario, calculer ton risque, documenter ta décision et comparer le résultat à ton plan initial",
            "Il réunit l’Analyse IA, le simulateur, le journal intelligent, le calculateur et le Trade Score. Ces outils complètent une formation de 500 pages, 21 phases, plus de 20 heures de contenu et six bonus",
        ],
        "highlight": "L’objectif n’est pas de multiplier les écrans, mais de rendre ton processus plus clair et plus mesurable",
        "cta": "Explorer le Trader Lab",
        "target_url": BASE_URL + "/trader-lab",
        "proof_items": [
            ("PRÉPARER", "Analyse IA + simulateur"),
            ("MESURER", "Journal + Trade Score"),
            ("PROTÉGER", "Calculateur de risque"),
        ],
    },
    {
        "stage": "jour-7", "delay_hours": 164,
        "subject": "Ce qui change quand tu n’avances plus seul",
        "preheader": "Le Canal VIP, les notifications et le support replacent l’humain au centre",
        "eyebrow": "LA COMMUNAUTÉ",
        "title": "Une académie doit aussi être un environnement humain",
        "body": [
            "Le Canal VIP rassemble les messages, les alertes, les résultats et les explications dans l’application. Les notifications peuvent être reçues sur iPhone ou Android après autorisation du membre",
            "À côté des outils, une équipe accompagne la prise en main et répond lorsque quelque chose n’est pas clair. Les témoignages vidéo et audio permettent aussi d’entendre les membres raconter leur expérience avec leurs propres mots",
        ],
        "highlight": "La technologie structure le parcours. La communauté évite qu’il devienne froid ou impersonnel",
        "cta": "Écouter les membres",
        "target_url": BASE_URL + "/vip#temoignages",
        "proof_items": [
            ("CANAL VIP", "Messages et alertes"),
            ("MOBILE", "iPhone et Android"),
            ("HUMAIN", "Support et onboarding"),
        ],
    },
    {
        "stage": "jour-10", "delay_hours": 236,
        "subject": "Quatre résultats que tu peux vérifier toi-même",
        "preheader": "Captures et messages vocaux sont disponibles dans leur format original",
        "eyebrow": "LES RÉSULTATS DES MEMBRES",
        "title": "Pas une promesse. Des expériences individuelles documentées",
        "body": [
            "Les témoignages publiés présentent notamment Warren, passé de 200 à 850 euros en 21 jours, Julien avec 9 013 euros partagés, Angel avec 1 600 euros en une journée et Samuel avec 760 euros dès sa première journée",
            "Leurs messages vocaux et leurs captures sont disponibles dans leur format original sur la présentation afin que tu puisses les écouter et les vérifier toi-même",
        ],
        "highlight": "Ces expériences sont individuelles et ne constituent ni une promesse de gains ni une garantie de résultats futurs",
        "cta": "Voir les captures et écouter les vocaux",
        "target_url": BASE_URL + "/vip#temoignages",
        "hero_image": BASE_URL + "/static/vip/assets/testimonial-proof-julien.jpg",
        "hero_alt": "Capture de résultat partagée par Julien",
        "proof_items": [
            ("WARREN", "200 € → 850 € en 21 jours"),
            ("JULIEN", "+9 013 € partagés"),
            ("ANGEL", "+1 600 € en une journée"),
            ("SAMUEL", "+760 € dès la première journée"),
        ],
        "disclaimer": "Résultats individuels présentés à titre de témoignage. Le trading comporte un risque de perte partielle ou totale du capital.",
    },
    {
        "stage": "jour-14", "delay_hours": 332,
        "subject": "La vidéo documentée est toujours visible",
        "preheader": "Regarde la preuve documentée dans son format original et fais-toi ton propre avis",
        "eyebrow": "LA PREUVE DOCUMENTÉE",
        "title": "Une vidéo vaut mieux qu’une longue promesse",
        "body": [
            "La présentation contient la vidéo des stratégies utilisées par Leris pour générer 800 000 euros en cinq jours. Elle est accompagnée de son parcours jusqu’à plus de 4 millions d’euros générés sur les marchés",
            "Cette preuve est accessible directement sur la page, dans son format original. Regarde-la tranquillement puis fais-toi ton propre avis sur l’expérience qui a conduit à la création de l’Académie",
        ],
        "highlight": "Une performance passée ne garantit aucun résultat futur. Elle documente un parcours, pas une promesse faite au prochain membre",
        "cta": "Regarder la vidéo originale",
        "target_url": BASE_URL + "/vip#preuve",
        "hero_image": BASE_URL + "/static/vip/assets/resultat-800k-poster.jpg",
        "hero_alt": "Aperçu de la vidéo documentée des 800 000 euros en cinq jours",
        "disclaimer": "Le trading comporte un risque de perte partielle ou totale du capital.",
    },
    {
        "stage": "jour-18", "delay_hours": 428,
        "subject": "Les réponses aux questions qu’on nous pose le plus",
        "preheader": "Explorer, mobile, Bectanse Auto, activation Stripe et support humain",
        "eyebrow": "LES OBJECTIONS",
        "title": "Tu dois comprendre ce que tu rejoins avant de payer",
        "body": [
            "Le compte Explorer reste en lecture seule. Il permet de voir l’application, mais aucune opération premium ne peut être lancée tant qu’un abonnement n’a pas été confirmé",
            "Bectanse Auto reste piloté selon tes paramètres. L’application fonctionne sur iPhone et Android, et l’accès membre se débloque uniquement après la confirmation réelle du paiement par Stripe",
        ],
        "highlight": "Si ta question est personnelle, le support peut vérifier ta situation avant que tu prennes une décision",
        "cta": "Voir les réponses et les offres",
        "target_url": BASE_URL + "/vip#offres",
        "show_support": True,
    },
    {
        "stage": "jour-21", "delay_hours": 500,
        "subject": "Dans 30 jours, qu’auras-tu réellement construit ?",
        "preheader": "Le même téléphone peut occuper tes journées ou soutenir une progression mesurable",
        "eyebrow": "LA PROJECTION",
        "title": "Consommer davantage ou construire quelque chose de durable",
        "body": [
            "Netflix, Spotify, les livraisons et les petites dépenses du quotidien disparaissent chaque mois sans laisser de méthode derrière elles",
            "Bectanse propose une autre utilisation de ce budget : 500 pages de connaissances, 21 phases, une application, des outils de suivi et un accompagnement humain conçus pour t’aider à construire progressivement ton autonomie",
        ],
        "highlight": "Le site estime ces dépenses courantes à 330 euros par mois. La vraie question n’est pas seulement ce que coûte l’Académie, mais ce que ton argent construit pour toi",
        "cta": "Comparer consommation et construction",
        "target_url": BASE_URL + "/vip#decision",
        "hero_image": BASE_URL + "/static/vip/assets/bectanse-brain-choice-v1.webp",
        "hero_alt": "Deux trajectoires : consommer ou construire",
    },
    {
        "stage": "jour-30", "delay_hours": 716,
        "subject": "Ton compte est prêt. Il reste à choisir ton rythme",
        "preheader": "Un mois, trois mois ou un an avec le même écosystème Bectanse",
        "eyebrow": "PASSER À L’ACTION",
        "title": "Tu n’as rien à recréer pour devenir membre",
        "body": [
            "Ton adresse est confirmée et ton code BCT existe déjà. Après un paiement Stripe confirmé, ce même compte passe automatiquement du mode Explorer à l’accès membre",
            "Les formules affichées sont de 500 euros pour un mois, 1 000 euros pour trois mois et 4 000 euros pour un an. L’écosystème reste le même, seule la durée de l’accompagnement change",
        ],
        "highlight": "Choisis le temps dont tu as réellement besoin, pas la formule la plus impressionnante",
        "cta": "Comparer les trois accompagnements",
        "target_url": BASE_URL + "/vip#offres",
        "proof_items": [
            ("1 MOIS", "500 €"),
            ("3 MOIS", "1 000 €"),
            ("1 AN", "4 000 €"),
        ],
    },
    {
        "stage": "jour-38", "delay_hours": 908,
        "subject": "{prenom}, est-ce qu’il te manque une réponse ?",
        "preheader": "Un dernier message humain, sans faux compte à rebours ni pression inutile",
        "eyebrow": "DERNIÈRE RELANCE",
        "title": "Je préfère une décision claire à une décision forcée",
        "body": [
            "Tu as maintenant vu l’histoire, le système, les outils, les membres, les preuves et les formules. Si Bectanse correspond à ce que tu veux construire, ton compte Explorer peut être activé sans nouvelle inscription",
            "Si ce n’est pas le bon moment, ton espace reste disponible. Et si une seule question te bloque encore, parle-nous avant de décider",
        ],
        "highlight": "Il n’y a pas de fausse urgence ici. La prochaine étape doit simplement être cohérente avec ton objectif",
        "cta": "Revoir les accès Bectanse",
        "target_url": BASE_URL + "/vip#offres",
        "show_support": True,
    },
]


EXPLORER_WEEKLY_CONTENT = [
    {
        "stage": "hebdo-systeme", "subject": "Ce que Bectanse remplace dans ton quotidien",
        "preheader": "Un seul environnement pour apprendre, préparer, suivre et mesurer",
        "eyebrow": "UN SEUL ÉCOSYSTÈME", "title": "Arrête de disperser tes outils",
        "body": ["L’application rassemble la formation, le risque, les analyses, le suivi et le Canal VIP", "Ton compte Explorer te permet de revoir la structure autant que nécessaire avant de débloquer les fonctions membres"],
        "highlight": "Un environnement cohérent est plus utile qu’une accumulation de contenus isolés", "cta": "Revoir l’écosystème complet",
        "target_url": BASE_URL + "/vip#formation",
    },
    {
        "stage": "hebdo-preuves", "subject": "Les preuves sont toujours visibles",
        "preheader": "Captures, messages vocaux et vidéo restent consultables dans leur format original",
        "eyebrow": "TÉMOIGNAGES DOCUMENTÉS", "title": "Regarde les retours dans leur format original",
        "body": ["Les témoignages audio, les captures membres et la vidéo documentée sont consultables sur la présentation", "Prends le temps de vérifier ce qui est montré avant de décider si l’Académie correspond à ton objectif"],
        "highlight": "Les performances passées et témoignages ne garantissent aucun résultat futur. Le trading comporte un risque de perte", "cta": "Consulter les témoignages",
        "target_url": BASE_URL + "/vip#temoignages",
    },
    {
        "stage": "hebdo-outils", "subject": "As-tu exploré les nouveaux outils Bectanse",
        "preheader": "Analyse IA, journal, simulateur et psychologie complètent le parcours",
        "eyebrow": "TRADER LAB", "title": "Analyse, journal, simulateur et psychologie",
        "body": ["Le Trader Lab a été conçu pour transformer une idée en processus observable", "L’Analyse IA, le journal intelligent, le simulateur et les exercices psychologiques complètent la formation et l’accompagnement"],
        "highlight": "Les outils sont visibles en Explorer puis utilisables avec l’accès membre", "cta": "Découvrir le Trader Lab",
        "target_url": BASE_URL + "/trader-lab",
    },
    {
        "stage": "hebdo-decision", "subject": "Ton compte est prêt si tu veux passer à l’action",
        "preheader": "Le paiement confirmé active directement le code BCT que tu possèdes déjà",
        "eyebrow": "PROCHAINE ÉTAPE", "title": "Tu n’as rien à recréer",
        "body": ["Ton code BCT et ton adresse confirmée sont déjà reliés", "Lorsque Stripe confirme ton abonnement, le même compte passe automatiquement du mode Explorer à l’accès membre"],
        "highlight": "Choisis uniquement la durée qui correspond à ton rythme. Le support peut t’aider si nécessaire", "cta": "Comparer les abonnements",
        "target_url": BASE_URL + "/vip#offres",
        "show_support": True,
    },
]


MEMBER_ONBOARDING_STAGES = [
    {
        "stage": "membre-jour-1", "delay_hours": 20,
        "subject": "{prenom}, configure ton espace Bectanse en quelques minutes",
        "eyebrow": "BIENVENUE DANS L'ACADÉMIE",
        "title": "Commence avec une base propre",
        "body": [
            "Ton abonnement est actif et ton compte BCT est maintenant relié à l'ensemble de l'Académie",
            "Installe l'application sur ton écran d'accueil, active les notifications puis vérifie ton profil afin de recevoir les informations importantes du Canal VIP",
        ],
        "highlight": "Ces premiers réglages évitent de manquer une alerte ou une mise à jour importante",
        "cta": "Configurer mon espace membre",
        "target_url": BASE_URL + "/accueil",
    },
    {
        "stage": "membre-jour-3", "delay_hours": 68,
        "subject": "Voici l'ordre conseillé pour utiliser l'Académie",
        "eyebrow": "TON PARCOURS MEMBRE",
        "title": "Ne consomme pas les contenus au hasard",
        "body": [
            "Commence par la méthode et la gestion du risque avant d'utiliser les outils plus avancés",
            "La formation, les guides et le Canal VIP ont été organisés pour te permettre de construire une routine claire plutôt que d'accumuler des informations",
        ],
        "highlight": "La progression vient de la répétition d'un processus cohérent, pas du nombre de pages ouvertes",
        "cta": "Continuer mon parcours",
        "target_url": BASE_URL + "/academie",
    },
    {
        "stage": "membre-jour-7", "delay_hours": 164,
        "subject": "As-tu déjà utilisé le Trader Lab",
        "eyebrow": "PASSER DE LA THÉORIE À LA PRATIQUE",
        "title": "Utilise les outils autour d'un même plan",
        "body": [
            "Le Trader Lab réunit l'Analyse IA, le journal intelligent, le simulateur, le calculateur et le Trade Score",
            "Ces outils ne remplacent pas ta décision. Ils servent à structurer ton analyse, ton risque et ton suivi",
        ],
        "highlight": "Teste un scénario, documente-le puis compare le résultat à ton plan initial",
        "cta": "Ouvrir le Trader Lab",
        "target_url": BASE_URL + "/trader-lab",
    },
    {
        "stage": "membre-jour-14", "delay_hours": 332,
        "subject": "Deux semaines dans Bectanse, fais le point",
        "eyebrow": "CONSTRUIRE UNE ROUTINE DURABLE",
        "title": "Ta psychologie mérite autant d'attention que ta technique",
        "body": [
            "Après deux semaines, vérifie ce que tu consultes vraiment, ce que tu appliques et ce qui te fait encore hésiter",
            "L'espace psychologie, le journal et le support sont là pour t'aider à identifier les décisions répétitives qui freinent ta progression",
        ],
        "highlight": "Si un élément de l'application ou de ton accès n'est pas clair, contacte le support directement depuis ton espace",
        "cta": "Faire le point sur mon parcours",
        "target_url": BASE_URL + "/dashboard#section-profil",
        "show_support": True,
    },
]


PENDING_OPTIN_STAGES = [
    {
        "stage": "confirmation-2h", "delay_hours": 2,
        "subject": "Il reste une étape pour ouvrir ton accès Explorer",
        "eyebrow": "CONFIRMATION EN ATTENTE", "title": "Ton espace n’est pas encore activé",
        "body": ["Tu as demandé un accès Explorer mais ton adresse e-mail n’a pas encore été confirmée", "Ouvre le premier message reçu et clique sur le lien. Si tu ne le retrouves pas, retourne sur le site pour demander un nouveau lien"],
        "highlight": "Aucun compte marketing n’est ajouté à Brevo tant que l’adresse n’est pas confirmée", "cta": "Renvoyer mon lien de confirmation", "target_url": BASE_URL + "/?explorer=confirmation",
    },
    {
        "stage": "confirmation-20h", "delay_hours": 20,
        "subject": "Ton lien Explorer va bientôt expirer",
        "eyebrow": "DERNIER RAPPEL DE CONFIRMATION", "title": "Active ton accès avant l’expiration du lien",
        "body": ["Le lien de confirmation reste valable pendant 24 heures", "Après cette échéance, tu pourras toujours demander un nouvel accès depuis le site mais cette courte série de rappels s’arrêtera"],
        "highlight": "La confirmation protège la qualité de la liste et empêche l’utilisation de fausses adresses", "cta": "Confirmer mon accès Explorer", "target_url": BASE_URL + "/?explorer=confirmation",
    },
]


CHECKOUT_STAGES = [
    {
        "stage": "rappel-1h", "delay_hours": 1,
        "subject": "Tu as ouvert le paiement sans aller au bout",
        "eyebrow": "TON ACCÈS EST À PORTÉE DE MAIN",
        "title": "Une question t’a peut-être arrêté",
        "body": [
            "Tu as consulté une page de paiement Bectanse mais aucun abonnement confirmé n’est encore rattaché à ton compte",
            "Si tu as simplement été interrompu, tu peux reprendre tranquillement. Si quelque chose n’était pas clair, le support est disponible",
        ],
        "highlight": "Ton compte actuel sera activé automatiquement dès que Stripe confirmera le paiement",
        "cta": "Reprendre mon inscription",
    },
    {
        "stage": "rappel-24h", "delay_hours": 24,
        "subject": "Avant de choisir, vérifie ces trois points",
        "eyebrow": "PRENDRE UNE DÉCISION CLAIRE",
        "title": "Le système doit correspondre à ce que tu recherches",
        "body": [
            "Bectanse réunit la formation, les outils, l’application, le Canal VIP et l’accompagnement dans un seul abonnement",
            "Tu conserves ton code BCT, tu accèdes depuis mobile et ton compte est relié automatiquement au paiement Stripe confirmé",
        ],
        "highlight": "Consulte la présentation complète et les témoignages avant de reprendre. Tu dois savoir exactement ce que tu rejoins",
        "cta": "Revoir la présentation complète",
    },
    {
        "stage": "rappel-72h", "delay_hours": 72,
        "subject": "On clôture ce rappel mais ton accès reste disponible",
        "eyebrow": "DERNIER RAPPEL DE PAIEMENT",
        "title": "Ne reste pas bloqué par une simple question",
        "body": [
            "Nous arrêtons ici cette courte série de rappels liée à ta visite sur Stripe",
            "La présentation et ton compte Explorer restent accessibles. Tu peux revenir quand tu le souhaites ou parler directement au support si tu veux une réponse humaine",
        ],
        "highlight": "Aucune relance de panier supplémentaire ne sera envoyée pour cette tentative",
        "cta": "Voir les accès disponibles",
    },
]


LEGACY_LEAD_STAGES = [
    {
        "stage": "reveil-1", "delay_hours": 1,
        "subject": "Bectanse a complètement changé depuis notre premier échange",
        "eyebrow": "UNE NOUVELLE PHASE",
        "title": "Ce que tu avais découvert n’existe plus sous cette forme",
        "body": [
            "Tu nous avais contactés pour découvrir Bectanse. Depuis, l’Académie a évolué à un niveau totalement différent",
            "Nous avons construit une véritable application membre avec Bectanse Auto, le Canal VIP, une formation de plus de 500 pages, l’Analyse IA, le Trader Lab, un journal intelligent, un simulateur et un espace psychologie",
        ],
        "highlight": "Tu peux maintenant créer gratuitement un compte Explorer et observer l’application avant de choisir un abonnement",
        "cta": "Découvrir gratuitement la nouvelle Académie",
    },
    {
        "stage": "reveil-2", "delay_hours": 72,
        "subject": "Voici tout ce que tu n’avais pas pu découvrir",
        "eyebrow": "BIEN PLUS QU’UNE FORMATION",
        "title": "Un système complet a été construit entre-temps",
        "body": [
            "L’objectif n’est plus de te donner du contenu isolé. Chaque outil communique avec le reste de ton parcours",
            "Tu peux apprendre, suivre le marché, travailler ton risque, consulter le Canal VIP, analyser une capture et retrouver tes progrès au même endroit depuis ton téléphone",
        ],
        "highlight": "Regarde la présentation, les témoignages audio, les captures membres et le fonctionnement de l’application puis fais-toi ton propre avis",
        "cta": "Voir tout ce qui a changé",
    },
    {
        "stage": "reveil-3", "delay_hours": 168,
        "subject": "Dernière invitation pour redécouvrir Bectanse",
        "eyebrow": "TON ACCÈS EXPLORER",
        "title": "Entre dans l’application sans payer",
        "body": [
            "C’est le dernier message de cette campagne de redécouverte",
            "Si le projet t’intéresse toujours, crée ton accès Explorer gratuit. Tu verras les sections, les outils et la structure réelle avant de décider si tu veux rejoindre les membres",
        ],
        "highlight": "Après cette invitation, nous arrêtons cette séquence. Tu pourras revenir librement quand tu le souhaites",
        "cta": "Créer mon accès Explorer gratuit",
    },
]


RENEWAL_STAGES = {
    "j-7": {
        "subject": "Ton accès arrive à échéance dans 7 jours", "eyebrow": "ANTICIPER SANS INTERRUPTION",
        "title": "Ton environnement Bectanse est toujours actif",
        "body": ["Ton accès arrive à échéance dans une semaine", "Si ton abonnement n’est pas renouvelé automatiquement par Stripe, tu peux préparer la suite dès maintenant et conserver la continuité de ton espace"],
        "highlight": "Tes réglages et ton historique restent associés à ton code BCT", "cta": "Gérer mon renouvellement",
    },
    "j-3": {
        "subject": "Plus que 3 jours avant la fin de ton accès", "eyebrow": "TON ACCÈS MEMBRE",
        "title": "Évite une coupure inutile",
        "body": ["Il reste trois jours avant l’échéance indiquée sur ton compte", "Renouvelle depuis la présentation officielle ou contacte le support si ta situation nécessite une vérification"],
        "highlight": "Après confirmation du paiement, le même compte est réactivé automatiquement", "cta": "Renouveler mon accès",
    },
    "j-1": {
        "subject": "Ton accès expire demain", "eyebrow": "DERNIÈRES 24 HEURES",
        "title": "Ton espace peut rester actif sans interruption",
        "body": ["Ton échéance est prévue demain", "Si tu souhaites continuer, tu peux choisir ta formule maintenant. Une question de paiement ou d’accès peut aussi être traitée par le support"],
        "highlight": "Le paiement confirmé par Stripe déclenche l’activation du compte", "cta": "Continuer avec Bectanse",
    },
    "j0": {
        "subject": "Ton accès arrive à échéance aujourd’hui", "eyebrow": "ÉCHÉANCE AUJOURD’HUI",
        "title": "Décide si tu souhaites poursuivre",
        "body": ["Ton accès membre arrive à sa date d’échéance", "Sans renouvellement confirmé, les fonctionnalités payantes seront verrouillées mais tes informations resteront conservées pour une future réactivation"],
        "highlight": "Tu ne dois pas recréer de compte pour reprendre", "cta": "Renouveler maintenant",
    },
}


REACTIVATION_STAGES = [
    {
        "stage": "expire-0", "delay_days": 0,
        "subject": "Ton espace est prêt à être réactivé", "eyebrow": "TON COMPTE EST CONSERVÉ",
        "title": "Tu peux reprendre là où tu t’étais arrêté",
        "body": ["Ton abonnement n’est plus actif mais ton compte BCT et tes informations sont toujours conservés", "Depuis ton départ, l’espace membre a évolué avec l’Analyse IA, le Trader Lab, le journal intelligent, le simulateur et une navigation mobile repensée"],
        "highlight": "Un paiement confirmé suffit pour réactiver les fonctionnalités membres", "cta": "Découvrir toutes les nouveautés",
    },
    {
        "stage": "expire-7", "delay_days": 7,
        "subject": "Tu n’as peut-être pas vu tout ce qui a changé", "eyebrow": "NOUVELLE EXPÉRIENCE BECTANSE",
        "title": "L’Académie ne ressemble plus à celle que tu as quittée",
        "body": ["L’application centralise désormais la progression, les outils de risque, l’analyse, le suivi et le Canal VIP", "Reconnecte-toi avec ton ancien code pour observer l’interface puis consulte la présentation si tu veux retrouver l’accès complet"],
        "highlight": "Ton compte reste identifiable même après l’expiration", "cta": "Redécouvrir l’Académie",
    },
    {
        "stage": "expire-14", "delay_days": 14,
        "subject": "Ton ancien compte peut reprendre vie", "eyebrow": "REVENIR SANS RECOMMENCER",
        "title": "Tout ton environnement est encore là",
        "body": ["Tu n’as pas besoin d’une nouvelle inscription ni d’un nouveau code", "Choisis une formule, utilise la même adresse e-mail et l’activation Stripe rattache automatiquement l’abonnement à ton compte"],
        "highlight": "Besoin d’aide avant de reprendre, le support peut vérifier ton compte", "cta": "Voir les formules de retour",
    },
    {
        "stage": "expire-21", "delay_days": 21,
        "subject": "Regarde les nouveautés avant de décider", "eyebrow": "PREUVES ET FONCTIONNALITÉS",
        "title": "Prends cinq minutes pour comparer",
        "body": ["La présentation officielle montre le fonctionnement de l’application, les outils, les témoignages audio et les captures membres", "Regarde les éléments documentés puis décide si le nouvel écosystème correspond mieux à ce que tu recherches aujourd’hui"],
        "highlight": "L’objectif n’est pas de te faire revenir à l’aveugle mais de te montrer ce qui existe réellement", "cta": "Voir la nouvelle présentation",
    },
    {
        "stage": "expire-30", "delay_days": 30,
        "subject": "Dernier message de cette série", "eyebrow": "TON COMPTE RESTE DISPONIBLE",
        "title": "Nous arrêtons les rappels de réactivation",
        "body": ["C’est le dernier e-mail de cette séquence", "Ton compte reste conservé et tu pourras revenir plus tard depuis la présentation officielle ou contacter le support si tu souhaites vérifier ton ancien accès"],
        "highlight": "Tu gardes la liberté de revenir quand le moment sera adapté", "cta": "Garder le lien de l’Académie",
    },
]


def _now():
    # Les colonnes PostgreSQL historiques sont des TIMESTAMP sans fuseau et
    # Railway enregistre NOW() en UTC. Les calculs doivent donc rester en UTC
    # naïf ; seule la fenêtre d'envoi et l'affichage utilisent Europe/Paris.
    return datetime.now(UTC_TZ).replace(tzinfo=None)


def ensure_marketing_schema(conn):
    """Schéma additive-only du moteur marketing."""
    conn.run("""CREATE TABLE IF NOT EXISTS marketing_contacts (
        member_code TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        first_name TEXT NOT NULL DEFAULT '',
        segment TEXT NOT NULL DEFAULT 'explorer',
        consent_source TEXT NOT NULL DEFAULT '',
        consent_at TIMESTAMP,
        unsubscribed_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""")
    conn.run("""CREATE INDEX IF NOT EXISTS marketing_contacts_segment_idx
        ON marketing_contacts (segment, updated_at DESC)""")
    conn.run("""CREATE INDEX IF NOT EXISTS marketing_contacts_email_idx
        ON marketing_contacts (LOWER(email))""")
    conn.run("""CREATE TABLE IF NOT EXISTS marketing_checkout_intents (
        id BIGSERIAL PRIMARY KEY,
        member_code TEXT NOT NULL,
        email TEXT NOT NULL,
        destination TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'direct',
        medium TEXT NOT NULL DEFAULT '',
        campaign TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        event_count INTEGER NOT NULL DEFAULT 1,
        started_at TIMESTAMP NOT NULL DEFAULT NOW(),
        last_started_at TIMESTAMP NOT NULL DEFAULT NOW(),
        converted_at TIMESTAMP,
        conversion_reference TEXT NOT NULL DEFAULT ''
    )""")
    conn.run("""ALTER TABLE marketing_checkout_intents
        ADD COLUMN IF NOT EXISTS stripe_session_id TEXT NOT NULL DEFAULT ''""")
    conn.run("""CREATE UNIQUE INDEX IF NOT EXISTS marketing_checkout_stripe_idx
        ON marketing_checkout_intents (stripe_session_id)
        WHERE stripe_session_id<>''""")
    conn.run("""CREATE INDEX IF NOT EXISTS marketing_checkout_open_idx
        ON marketing_checkout_intents (member_code, status, started_at DESC)""")
    conn.run("""CREATE TABLE IF NOT EXISTS marketing_email_log (
        id BIGSERIAL PRIMARY KEY,
        member_code TEXT NOT NULL,
        recipient_email TEXT NOT NULL,
        journey TEXT NOT NULL,
        stage TEXT NOT NULL,
        reference_key TEXT NOT NULL DEFAULT 'default',
        subject TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        provider_message_id TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        due_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        sent_at TIMESTAMP,
        UNIQUE (member_code, journey, stage, reference_key)
    )""")
    conn.run("""ALTER TABLE marketing_email_log
        ADD COLUMN IF NOT EXISTS provider_status TEXT NOT NULL DEFAULT ''""")
    conn.run("""ALTER TABLE marketing_email_log
        ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMP""")
    conn.run("""ALTER TABLE marketing_email_log
        ADD COLUMN IF NOT EXISTS clicked_at TIMESTAMP""")
    conn.run("""CREATE INDEX IF NOT EXISTS marketing_email_log_status_idx
        ON marketing_email_log (status, sent_at DESC, created_at DESC)""")
    conn.run("""CREATE TABLE IF NOT EXISTS marketing_settings (
        id INTEGER PRIMARY KEY CHECK (id=1),
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        daily_send_limit INTEGER NOT NULL DEFAULT 180,
        weekly_contact_limit INTEGER NOT NULL DEFAULT 4,
        min_gap_hours INTEGER NOT NULL DEFAULT 20,
        batch_limit INTEGER NOT NULL DEFAULT 30,
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""")
    conn.run("""ALTER TABLE marketing_settings
        ADD COLUMN IF NOT EXISTS legacy_campaign_enabled BOOLEAN NOT NULL DEFAULT TRUE""")
    conn.run("""ALTER TABLE marketing_settings ALTER COLUMN enabled SET DEFAULT TRUE""")
    conn.run("""ALTER TABLE marketing_settings
        ALTER COLUMN legacy_campaign_enabled SET DEFAULT TRUE""")
    conn.run("""ALTER TABLE marketing_settings
        ADD COLUMN IF NOT EXISTS legacy_daily_limit INTEGER NOT NULL DEFAULT 1000""")
    conn.run("""ALTER TABLE marketing_settings
        ADD COLUMN IF NOT EXISTS legacy_started_at TIMESTAMP""")
    conn.run("""ALTER TABLE marketing_settings
        ADD COLUMN IF NOT EXISTS legacy_paused_reason TEXT NOT NULL DEFAULT ''""")
    conn.run("""ALTER TABLE marketing_settings
        ADD COLUMN IF NOT EXISTS last_run_at TIMESTAMP""")
    conn.run("""ALTER TABLE marketing_settings
        ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMP""")
    conn.run("""ALTER TABLE marketing_settings
        ADD COLUMN IF NOT EXISTS last_error TEXT NOT NULL DEFAULT ''""")
    conn.run("""ALTER TABLE marketing_settings
        ADD COLUMN IF NOT EXISTS last_sent_count INTEGER NOT NULL DEFAULT 0""")
    conn.run("""INSERT INTO marketing_settings (id) VALUES (1)
        ON CONFLICT (id) DO NOTHING""")
    conn.run("""CREATE TABLE IF NOT EXISTS marketing_legacy_leads (
        id BIGSERIAL PRIMARY KEY,
        email TEXT NOT NULL,
        first_name TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'anciens-prospects-2025',
        status TEXT NOT NULL DEFAULT 'active',
        imported_at TIMESTAMP NOT NULL DEFAULT NOW(),
        converted_at TIMESTAMP,
        unsubscribed_at TIMESTAMP,
        last_contact_at TIMESTAMP
    )""")
    conn.run("""ALTER TABLE marketing_legacy_leads
        ADD COLUMN IF NOT EXISTS phone TEXT NOT NULL DEFAULT ''""")
    conn.run("""ALTER TABLE marketing_legacy_leads
        ADD COLUMN IF NOT EXISTS country TEXT NOT NULL DEFAULT ''""")
    conn.run("""ALTER TABLE marketing_legacy_leads
        ADD COLUMN IF NOT EXISTS collected_at TIMESTAMP""")
    conn.run("""ALTER TABLE marketing_legacy_leads
        ADD COLUMN IF NOT EXISTS consent_basis TEXT NOT NULL DEFAULT 'demande-formulaire'""")
    conn.run("""ALTER TABLE marketing_legacy_leads
        ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMP""")
    conn.run("""ALTER TABLE marketing_legacy_leads
        ADD COLUMN IF NOT EXISTS clicked_at TIMESTAMP""")
    conn.run("""ALTER TABLE marketing_legacy_leads
        ADD COLUMN IF NOT EXISTS bounced_at TIMESTAMP""")
    conn.run("""CREATE UNIQUE INDEX IF NOT EXISTS marketing_legacy_leads_email_idx
        ON marketing_legacy_leads (LOWER(email))""")
    conn.run("""CREATE TABLE IF NOT EXISTS marketing_email_events (
        id BIGSERIAL PRIMARY KEY,
        event_hash TEXT UNIQUE NOT NULL,
        recipient_email TEXT NOT NULL DEFAULT '',
        event_type TEXT NOT NULL,
        provider_message_id TEXT NOT NULL DEFAULT '',
        journey TEXT NOT NULL DEFAULT '',
        stage TEXT NOT NULL DEFAULT '',
        event_at TIMESTAMP NOT NULL DEFAULT NOW(),
        payload TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""")
    conn.run("""CREATE INDEX IF NOT EXISTS marketing_email_events_type_idx
        ON marketing_email_events (event_type,event_at DESC)""")
    conn.run("""CREATE TABLE IF NOT EXISTS marketing_problem_alerts (
        alert_key TEXT PRIMARY KEY,
        last_sent_at TIMESTAMP,
        occurrences INTEGER NOT NULL DEFAULT 0,
        last_message TEXT NOT NULL DEFAULT '',
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""")


def _notify_marketing_problem(conn, notify_admin, alert_key, message,
                              cooldown_hours=6):
    """Alerte l'administrateur une seule fois par incident et par période."""
    if not notify_admin:
        return False
    rows = conn.run("""SELECT last_sent_at FROM marketing_problem_alerts
        WHERE alert_key=:key""", key=alert_key)
    last_sent_at = rows[0][0] if rows else None
    should_send = not last_sent_at or (_now() - last_sent_at) >= timedelta(
        hours=cooldown_hours)
    conn.run("""INSERT INTO marketing_problem_alerts
        (alert_key,last_sent_at,occurrences,last_message,updated_at)
        VALUES (:key,:sent,1,:message,NOW())
        ON CONFLICT (alert_key) DO UPDATE SET
            last_sent_at=CASE WHEN :send THEN NOW()
                ELSE marketing_problem_alerts.last_sent_at END,
            occurrences=marketing_problem_alerts.occurrences+1,
            last_message=:message,updated_at=NOW()""",
        key=alert_key, sent=_now() if should_send else None,
        send=should_send, message=str(message)[:1000])
    if not should_send:
        return False
    try:
        notify_admin(
            "🚨 *AUTOMATISATION MARKETING*\n\n" + str(message)[:900] +
            "\n\nAucune action n'est nécessaire si le problème disparaît au prochain passage."
        )
        return True
    except Exception:
        return False


def sync_marketing_segments(conn):
    """Recalcule les segments sans mélanger prospects, actifs et expirés."""
    ensure_marketing_schema(conn)
    conn.run("""INSERT INTO marketing_contacts
        (member_code,email,first_name,segment,consent_source,consent_at,created_at,updated_at)
        SELECT m.code,LOWER(TRIM(m.email)),SPLIT_PART(TRIM(m.nom),' ',1),
            CASE
                WHEN COALESCE(m.admin_suspended,FALSE) THEN 'suspended'
                WHEN COALESCE(m.billing_status,'') IN ('past_due','unpaid','canceled','paused')
                     AND COALESCE(m.stripe_subscription_id,'')<>'' THEN 'expired'
                WHEN COALESCE(m.access_level,'member') IN ('explorer','demo') THEN 'explorer'
                WHEN m.actif=FALSE OR (m.date_fin IS NOT NULL AND m.date_fin<=NOW()) THEN 'expired'
                WHEN m.date_fin IS NOT NULL AND m.date_fin<=NOW()+INTERVAL '7 days'
                     AND (COALESCE(m.stripe_subscription_id,'')=''
                          OR COALESCE(m.billing_cancel_at_period_end,FALSE)) THEN 'expiring'
                ELSE 'active'
            END,
            CASE WHEN COALESCE(m.access_level,'member') IN ('explorer','demo')
                 THEN 'double-opt-in-explorer' ELSE 'relation-membre' END,
            COALESCE(m.email_verified_at,m.created_at),m.created_at,NOW()
        FROM members m
        WHERE m.code<>'BCT-DEMO2026' AND m.email IS NOT NULL
          AND TRIM(m.email)<>'' AND m.email_verified_at IS NOT NULL
        ON CONFLICT (member_code) DO UPDATE SET
            email=EXCLUDED.email,first_name=EXCLUDED.first_name,
            segment=EXCLUDED.segment,consent_source=EXCLUDED.consent_source,
            consent_at=COALESCE(marketing_contacts.consent_at,EXCLUDED.consent_at),
            updated_at=NOW()""")
    conn.run("""UPDATE marketing_legacy_leads leads SET status='converted',converted_at=NOW()
        WHERE status='active' AND EXISTS (
            SELECT 1 FROM marketing_contacts contacts
            WHERE LOWER(contacts.email)=LOWER(leads.email)
        )""")


def upsert_marketing_contact_for_member(conn, member_code):
    ensure_marketing_schema(conn)
    sync_marketing_segments(conn)
    return conn.run("""SELECT member_code,segment FROM marketing_contacts
        WHERE member_code=:code""", code=member_code)


def record_checkout_start(conn, member_code, destination="", source="direct", medium="", campaign=""):
    """Associe un clic Stripe au compte connecté, sans stocker un visiteur anonyme."""
    if not member_code:
        return None
    ensure_marketing_schema(conn)
    rows = conn.run("""SELECT email FROM members WHERE code=:code
        AND email_verified_at IS NOT NULL AND TRIM(COALESCE(email,''))<>''""", code=member_code)
    if not rows:
        return None
    email = str(rows[0][0]).strip().lower()
    open_rows = conn.run("""SELECT id FROM marketing_checkout_intents
        WHERE member_code=:code AND status='open'
          AND last_started_at>NOW()-INTERVAL '7 days'
        ORDER BY started_at DESC LIMIT 1""", code=member_code)
    if open_rows:
        intent_id = int(open_rows[0][0])
        conn.run("""UPDATE marketing_checkout_intents SET
            destination=:destination,source=:source,medium=:medium,campaign=:campaign,
            last_started_at=NOW(),event_count=event_count+1 WHERE id=:id""",
            destination=str(destination or "")[:500], source=str(source or "direct")[:100],
            medium=str(medium or "")[:100], campaign=str(campaign or "")[:120], id=intent_id)
        return intent_id
    created = conn.run("""INSERT INTO marketing_checkout_intents
        (member_code,email,destination,source,medium,campaign)
        VALUES (:code,:email,:destination,:source,:medium,:campaign) RETURNING id""",
        code=member_code, email=email, destination=str(destination or "")[:500],
        source=str(source or "direct")[:100], medium=str(medium or "")[:100],
        campaign=str(campaign or "")[:120])
    return int(created[0][0]) if created else None


def record_checkout_session(conn, member_code, stripe_session_id, destination="",
                            source="direct", medium="", campaign=""):
    """Relie la session Stripe réellement créée à l'intention marketing ouverte."""
    intent_id = record_checkout_start(
        conn, member_code, destination=destination, source=source,
        medium=medium, campaign=campaign,
    )
    if intent_id and stripe_session_id:
        conn.run("""UPDATE marketing_checkout_intents SET stripe_session_id=:session
            WHERE id=:id""", session=str(stripe_session_id)[:160], id=intent_id)
    return intent_id


def mark_checkout_expired(conn, stripe_session_id):
    """Conserve l'intention ouverte : elle devient éligible à la séquence d'abandon."""
    ensure_marketing_schema(conn)
    if not stripe_session_id:
        return
    conn.run("""UPDATE marketing_checkout_intents SET last_started_at=NOW()
        WHERE stripe_session_id=:session AND status='open'""",
        session=str(stripe_session_id)[:160])


def mark_marketing_conversion(conn, member_code, email="", reference=""):
    """Stoppe toutes les relances de paiement dès la confirmation Stripe."""
    ensure_marketing_schema(conn)
    if member_code:
        conn.run("""UPDATE marketing_checkout_intents SET status='converted',
            converted_at=NOW(),conversion_reference=:reference
            WHERE member_code=:code AND status='open'""",
            reference=str(reference or "")[:160], code=member_code)
        conn.run("""UPDATE marketing_contacts SET segment='active',updated_at=NOW()
            WHERE member_code=:code""", code=member_code)
    elif email:
        conn.run("""UPDATE marketing_checkout_intents SET status='converted',
            converted_at=NOW(),conversion_reference=:reference
            WHERE LOWER(email)=LOWER(:email) AND status='open'""",
            reference=str(reference or "")[:160], email=email)


def _utm_url(journey, stage, anchor=""):
    query = urlencode({
        "utm_source": "brevo", "utm_medium": "email",
        "utm_campaign": "bectanse_" + journey.replace("_", "-"),
        "utm_content": stage,
    })
    suffix = ("#" + anchor.lstrip("#")) if anchor else ""
    return f"{BASE_URL}/vip?{query}{suffix}"


def _tracked_email_url(raw_url, journey, stage):
    """Ajoute le suivi e-mail sans casser le chemin, les paramètres ou l’ancre."""
    parts = urlsplit(raw_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({
        "utm_source": "brevo",
        "utm_medium": "email",
        "utm_campaign": "bectanse_" + journey.replace("_", "-"),
        "utm_content": stage,
    })
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(query), parts.fragment))


def _email_header():
    return f"""<table role='presentation' cellspacing='0' cellpadding='0' border='0'>
      <tr><td style='padding-right:11px'>
        <img src='{BASE_URL}/static/icons/bectanse-app-icon-master.png' width='42' height='42'
          alt='Bectanse Académie' style='display:block;width:42px;height:42px;border:0;border-radius:11px'>
      </td><td style='font-family:Arial,Helvetica,sans-serif;font-size:20px;font-weight:900;letter-spacing:.8px;color:#ffffff'>
        BECTANSE<br><span style='font-size:12px;letter-spacing:2.4px;color:#ff6a1f'>ACADÉMIE</span>
      </td></tr>
    </table>"""


def _email_hero(content):
    source = content.get("hero_image")
    if not source:
        return ""
    safe_source = html.escape(source, quote=True)
    safe_alt = html.escape(content.get("hero_alt", "Bectanse Académie"), quote=True)
    return f"""<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0'
      style='width:100%;margin:0 0 26px'><tr><td>
      <img src='{safe_source}' width='572' alt='{safe_alt}' class='mail-image'
        style='display:block;width:100%;max-width:572px;height:auto;border:0;border-radius:17px;background:#171a17'>
    </td></tr></table>"""


def _email_proof_cards(content):
    items = content.get("proof_items") or []
    if not items:
        return ""
    rows = []
    for label, value in items:
        rows.append(f"""<tr><td style='padding:12px 14px;border-bottom:1px solid #292d28'>
          <span style='display:block;margin-bottom:4px;color:#ff7b3d;font-size:9px;line-height:1.3;font-weight:900;letter-spacing:1.3px'>{html.escape(label)}</span>
          <strong style='display:block;color:#f4f5f2;font-size:14px;line-height:1.45'>{html.escape(value)}</strong>
        </td></tr>""")
    return f"""<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0'
      style='width:100%;margin:5px 0 25px;border:1px solid #292d28;border-radius:14px;background:#131613'>
      {''.join(rows)}
    </table>"""


def _email_cta(label, url):
    safe_label = html.escape(label)
    safe_url = html.escape(url, quote=True)
    return f"""<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0'
      style='width:100%'><tr><td align='center' bgcolor='#ff641f' class='mail-cta'
      style='border-radius:13px;background:#ff641f'>
      <a href='{safe_url}' style='display:block;padding:17px 18px;color:#ffffff;text-decoration:none;
        text-align:center;font-size:15px;line-height:1.25;font-weight:900'>{safe_label}&nbsp;&nbsp;→</a>
    </td></tr></table>"""


def _email_signature(show_support=False):
    support = ""
    if show_support:
        support = ("<p style='margin:16px 0 0;color:#8d928a;font-size:12px;line-height:1.55'>"
                   "Une question précise&nbsp;? <a href='" + SUPPORT_URL + "' "
                   "style='color:#ff8b54;text-decoration:none;font-weight:700'>Parler au support</a></p>")
    return f"""<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0'
      style='width:100%;margin-top:28px'><tr><td style='padding-top:22px;border-top:1px solid #292d28'>
      <p style='margin:0;color:#f4f5f2;font-size:13px;line-height:1.55;font-weight:800'>Leris Luketo</p>
      <p style='margin:2px 0 0;color:#70756e;font-size:11px;line-height:1.55'>Fondateur de Bectanse Académie</p>
      {support}
    </td></tr></table>"""


def _email_footer(unsubscribe_url):
    safe_url = html.escape(unsubscribe_url, quote=True)
    return f"""<tr><td align='center' style='padding:24px 16px 8px;color:#575b55;
      font-family:Arial,Helvetica,sans-serif;font-size:10px;line-height:1.65'>
      Bectanse Académie · LERIS CORP FZCO<br>
      Message lié à ton inscription ou à ta relation membre
    </td></tr>
    <tr><td align='center' style='padding:0 16px 22px;font-family:Arial,Helvetica,sans-serif;
      font-size:9px;line-height:1.5'>
      <a href='{safe_url}' style='color:#4f534d;text-decoration:underline'>se désinscrire</a>
    </td></tr>"""


def _personalized_subject(template, first_name):
    clean_name = str(first_name or "").strip()
    if clean_name:
        return template.format(prenom=clean_name)
    return template.replace("{prenom}, ", "").replace("{prenom}", "Bonjour").strip()


def _email_html(prenom, content, journey, stage, unsubscribe_url):
    clean_name = str(prenom or "").strip()
    greeting = "Bonjour" + (" " + html.escape(clean_name) if clean_name else "")
    cta_url = _tracked_email_url(
        content.get("target_url") or _utm_url(
            journey, stage,
            "offres" if journey not in {"explorer", "legacy_reactivation"} else "capture",
        ),
        journey,
        stage,
    )
    paragraphs = "".join(
        f"<p style='margin:0 0 17px;color:#c8cbc6;font-size:15px;line-height:1.72'>{html.escape(text)}</p>"
        for text in content["body"]
    )
    disclaimer = ""
    if content.get("disclaimer"):
        disclaimer = ("<p style='margin:17px 0 0;color:#777c74;font-size:10px;line-height:1.55'>"
                      + html.escape(content["disclaimer"]) + "</p>")
    preheader = html.escape(content.get("preheader") or content["highlight"])
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<meta name='x-apple-disable-message-reformatting'>
<meta name='color-scheme' content='dark'><meta name='supported-color-schemes' content='dark'>
<style>
  body,table,td,a{{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}}
  table,td{{mso-table-lspace:0pt;mso-table-rspace:0pt;border-collapse:separate}}
  img{{-ms-interpolation-mode:bicubic}}
  @media(max-width:620px){{
    .mail-shell{{padding:12px 9px!important}}
    .mail-body{{padding:27px 20px!important}}
    .mail-title{{font-size:31px!important;line-height:1.06!important}}
    .mail-cta a{{font-size:14px!important;padding:16px 13px!important}}
    .mail-image{{border-radius:13px!important}}
  }}
</style></head>
<body style='margin:0;padding:0;background:#050605;font-family:Arial,Helvetica,sans-serif;color:#ffffff'>
<div style='display:none!important;visibility:hidden;mso-hide:all;max-height:0;max-width:0;
  overflow:hidden;opacity:0;color:transparent'>{preheader}&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;</div>
<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0'
  style='width:100%;background:#050605'><tr><td align='center'>
  <table role='presentation' width='640' cellspacing='0' cellpadding='0' border='0' class='mail-shell'
    style='width:100%;max-width:640px;padding:28px 16px'>
    <tr><td align='center' style='padding:12px 8px 24px'>{_email_header()}</td></tr>
    <tr><td style='border:1px solid #292d28;border-radius:24px;background:#101210;overflow:hidden'>
      <div style='height:4px;line-height:4px;font-size:4px;background:#ff641f'>&nbsp;</div>
      <div class='mail-body' style='padding:38px 34px'>
        <div style='display:inline-block;margin:0 0 15px;padding:7px 10px;border:1px solid #4b2b1d;
          border-radius:999px;background:#1b120e;color:#ff864c;font-size:10px;line-height:1.3;
          letter-spacing:1.5px;font-weight:900'>{html.escape(content['eyebrow'])}</div>
        <p style='margin:0 0 9px;color:#888e86;font-size:14px;line-height:1.5'>{greeting}</p>
        <h1 class='mail-title' style='margin:0 0 24px;color:#ffffff;font-size:36px;line-height:1.04;
          letter-spacing:-1.1px'>{html.escape(content['title'])}</h1>
        {_email_hero(content)}
        {paragraphs}
        {_email_proof_cards(content)}
        <div style='margin:26px 0;padding:19px 20px;border:1px solid #343a32;border-left:4px solid #ff641f;
          border-radius:15px;background:#161916;color:#f1f2ef;font-size:14px;line-height:1.62;font-weight:700'>
          {html.escape(content['highlight'])}</div>
        {_email_cta(content['cta'], cta_url)}
        {disclaimer}
        {_email_signature(bool(content.get('show_support')))}
      </div>
    </td></tr>
    {_email_footer(unsubscribe_url)}
  </table>
</td></tr></table></body></html>"""


def _already_sent(conn, code, journey, stage, reference):
    return bool(conn.run("""SELECT 1 FROM marketing_email_log
        WHERE member_code=:code AND journey=:journey AND stage=:stage
          AND reference_key=:reference AND status='sent' LIMIT 1""",
        code=code, journey=journey, stage=stage, reference=reference))


def _contact_rate_allowed(conn, code, weekly_limit, min_gap_hours):
    sent_7d = int(conn.run("""SELECT COUNT(*) FROM marketing_email_log
        WHERE member_code=:code AND status='sent'
          AND sent_at>NOW()-INTERVAL '7 days'""", code=code)[0][0] or 0)
    if sent_7d >= weekly_limit:
        return False
    last_rows = conn.run("""SELECT MAX(sent_at) FROM (
        SELECT sent_at FROM marketing_email_log WHERE member_code=:code AND status='sent'
        UNION ALL
        SELECT sent_at FROM renewal_email_log WHERE member_code=:code AND status='sent'
    ) history""", code=code)
    last_sent = last_rows[0][0] if last_rows else None
    return not last_sent or (_now() - last_sent) >= timedelta(hours=min_gap_hours)


def _checkout_candidate(conn, contact):
    code = contact[0]
    rows = conn.run("""SELECT id,started_at FROM marketing_checkout_intents
        WHERE member_code=:code AND status='open'
        ORDER BY started_at DESC LIMIT 1""", code=code)
    if not rows:
        return None
    intent_id, started_at = int(rows[0][0]), rows[0][1]
    age_hours = (_now() - started_at).total_seconds() / 3600
    reference = str(intent_id)
    for content in CHECKOUT_STAGES:
        if age_hours >= content["delay_hours"] and not _already_sent(
                conn, code, "checkout_abandon", content["stage"], reference):
            return "checkout_abandon", content, reference, started_at + timedelta(hours=content["delay_hours"])
    return None


def _renewal_candidate(conn, contact):
    code, _email, _first, segment, _created, date_fin = contact[:6]
    if not date_fin:
        return None
    reference = date_fin.date().isoformat() if hasattr(date_fin, "date") else str(date_fin)[:10]
    if segment == "expiring":
        days = (date_fin.date() - _now().date()).days
        for threshold, stage in ((0, "j0"), (1, "j-1"), (3, "j-3"), (7, "j-7")):
            if days <= threshold and not _already_sent(conn, code, "renewal", stage, reference):
                return "renewal", {"stage": stage, **RENEWAL_STAGES[stage]}, reference, _now()
        return None
    if segment != "expired":
        return None
    initial = REACTIVATION_STAGES[0]
    if not _already_sent(conn, code, "reactivation", initial["stage"], reference):
        return "reactivation", initial, reference, _now()
    rows = conn.run("""SELECT sent_at FROM marketing_email_log
        WHERE member_code=:code AND journey='reactivation' AND stage='expire-0'
          AND reference_key=:reference AND status='sent' LIMIT 1""", code=code, reference=reference)
    base = rows[0][0] if rows else _now()
    for content in REACTIVATION_STAGES[1:]:
        due = base + timedelta(days=content["delay_days"])
        if _now() >= due and not _already_sent(
                conn, code, "reactivation", content["stage"], reference):
            return "reactivation", content, reference, due
    return None


def _explorer_candidate(conn, contact):
    code, _email, _first, segment, created_at = contact[:5]
    if segment != "explorer":
        return None
    age_hours = (_now() - created_at).total_seconds() / 3600
    for content in EXPLORER_STAGES:
        if age_hours >= content["delay_hours"] and not _already_sent(
                conn, code, "explorer", content["stage"], "onboarding"):
            return "explorer", content, "onboarding", created_at + timedelta(hours=content["delay_hours"])
    initial_sent = int(conn.run("""SELECT COUNT(DISTINCT stage) FROM marketing_email_log
        WHERE member_code=:code AND journey='explorer' AND reference_key='onboarding'
          AND status='sent'""", code=code)[0][0] or 0)
    if initial_sent >= len(EXPLORER_STAGES):
        week_number = max(4, int((_now() - created_at).days // 7))
        content = dict(EXPLORER_WEEKLY_CONTENT[(week_number - 4) % len(EXPLORER_WEEKLY_CONTENT)])
        reference = f"week-{week_number}"
        if not _already_sent(conn, code, "explorer_weekly", content["stage"], reference):
            return "explorer_weekly", content, reference, _now()
    return None


def _member_onboarding_candidate(conn, contact):
    code, _email, _first, segment, created_at = contact[:5]
    if segment != "active":
        return None
    activated_at = contact[9] if len(contact) > 9 and contact[9] else created_at
    # Ce parcours accompagne uniquement les nouvelles activations. Il ne doit
    # pas réveiller rétroactivement tous les membres historiques.
    if activated_at < MEMBER_ONBOARDING_START:
        return None
    age_hours = (_now() - activated_at).total_seconds() / 3600
    reference = activated_at.date().isoformat() if hasattr(activated_at, "date") else str(activated_at)[:10]
    for content in MEMBER_ONBOARDING_STAGES:
        if age_hours >= content["delay_hours"] and not _already_sent(
                conn, code, "member_onboarding", content["stage"], reference):
            return ("member_onboarding", content, reference,
                    activated_at + timedelta(hours=content["delay_hours"]))
    return None


def _pending_optin_candidate(conn, contact):
    code, _email, _first, segment, created_at = contact[:5]
    if segment != "pending_optin":
        return None
    age_hours = (_now() - created_at).total_seconds() / 3600
    reference = created_at.strftime("%Y%m%d%H%M")
    for content in PENDING_OPTIN_STAGES:
        if age_hours >= content["delay_hours"] and not _already_sent(
                conn, code, "email_confirmation", content["stage"], reference):
            return ("email_confirmation", content, reference,
                    created_at + timedelta(hours=content["delay_hours"]))
    return None


def _legacy_lead_candidate(conn, contact):
    code, _email, _first, segment, created_at = contact[:5]
    if segment != "legacy_lead":
        return None
    age_hours = (_now() - created_at).total_seconds() / 3600
    lead_id = int(code[5:])
    engagement = conn.run("""SELECT clicked_at FROM marketing_legacy_leads
        WHERE id=:id AND status='active' AND unsubscribed_at IS NULL""", id=lead_id)
    if not engagement:
        return None
    clicked_at = engagement[0][0]
    for content in LEGACY_LEAD_STAGES:
        # Le troisième message est réservé aux personnes qui ont réellement
        # cliqué. Les autres reçoivent les deux prises de contact annoncées,
        # puis la séquence s'arrête pour préserver la délivrabilité.
        if content["stage"] == "reveil-3" and not clicked_at:
            continue
        if age_hours >= content["delay_hours"] and not _already_sent(
                conn, code, "legacy_reactivation", content["stage"], "2025-relaunch"):
            return ("legacy_reactivation", content, "2025-relaunch",
                    created_at + timedelta(hours=content["delay_hours"]))
    return None


def _legacy_delivery_health(conn):
    sent = int(conn.run("""SELECT COUNT(*) FROM marketing_email_log
        WHERE journey='legacy_reactivation' AND status='sent'
          AND sent_at>NOW()-INTERVAL '24 hours'""")[0][0] or 0)
    counts = {str(row[0]).lower(): int(row[1]) for row in conn.run("""
        SELECT event_type,COUNT(*) FROM marketing_email_events
        WHERE journey='legacy_reactivation' AND event_at>NOW()-INTERVAL '24 hours'
        GROUP BY event_type""")}
    # Une adresse déjà bloquée par Brevo (désinscription historique ou liste
    # repoussoir) est immédiatement supprimée du parcours, mais ne constitue
    # pas un hard bounce susceptible de dégrader la réputation d'envoi.
    hard = sum(counts.get(name, 0) for name in ("hardbounce", "invalid"))
    spam = counts.get("spam", 0)
    unsubscribed = counts.get("unsubscribed", 0)
    health = {"sent": sent, "hard": hard, "spam": spam, "unsubscribed": unsubscribed}
    if sent < 50:
        return True, "", health
    hard_rate = hard * 100 / sent
    spam_rate = spam * 100 / sent
    unsubscribe_rate = unsubscribed * 100 / sent
    # Un seul désabonnement sur un très petit échantillon ne doit pas bloquer
    # toute une base. Les taux deviennent décisionnels avec un volume minimum,
    # tandis qu'une plainte spam reste toujours un signal d'arrêt immédiat.
    if hard >= 3 and hard_rate >= 1.5:
        return False, f"Pause automatique: rebonds définitifs {hard_rate:.2f}%", health
    if sent >= 200 and unsubscribed >= 3 and unsubscribe_rate >= 0.8:
        return False, f"Pause automatique: désabonnements {unsubscribe_rate:.2f}%", health
    if spam >= 1 and spam_rate >= 0.1:
        return False, f"Pause automatique: plaintes {spam_rate:.2f}%", health
    return True, "", health


def _claim_email(conn, contact, journey, content, reference, due_at):
    code, email = contact[0], contact[1]
    return conn.run("""INSERT INTO marketing_email_log
        (member_code,recipient_email,journey,stage,reference_key,subject,status,due_at)
        VALUES (:code,:email,:journey,:stage,:reference,:subject,'pending',:due_at)
        ON CONFLICT (member_code,journey,stage,reference_key) DO UPDATE SET
            status='pending',error='',created_at=NOW(),due_at=:due_at
        WHERE marketing_email_log.status='failed'
          AND marketing_email_log.created_at<NOW()-INTERVAL '2 hours'
        RETURNING id""", code=code, email=email, journey=journey,
        stage=content["stage"], reference=reference,
        subject=content["subject"][:250], due_at=due_at)


def run_marketing_automation(get_conn, send_email, action_token, dry_run=False,
                             force=False, notify_admin=None):
    """Sélectionne puis envoie au maximum un message pertinent par contact."""
    conn = get_conn()
    try:
        ensure_marketing_schema(conn)
        sync_marketing_segments(conn)
        settings = conn.run("""SELECT enabled,daily_send_limit,weekly_contact_limit,
            min_gap_hours,batch_limit,legacy_campaign_enabled,legacy_daily_limit,
            legacy_started_at,legacy_paused_reason FROM marketing_settings WHERE id=1""")[0]
        (enabled, daily_limit, weekly_limit, min_gap, batch_limit,
         legacy_enabled, legacy_daily_limit, legacy_started_at,
         legacy_paused_reason) = settings
        if not enabled and not dry_run:
            conn.run("""UPDATE marketing_settings SET last_run_at=NOW(),
                last_error='Automatisation générale en pause',updated_at=NOW()
                WHERE id=1""")
            _notify_marketing_problem(
                conn, notify_admin, "engine-paused",
                "Le moteur général est en pause. Les parcours Explorer, panier, renouvellement et réactivation ne peuvent pas avancer.",
                cooldown_hours=24,
            )
            return {"ok": True, "paused": True, "sent": 0, "candidates": []}
        if (not dry_run and not legacy_enabled and
                str(legacy_paused_reason or "").startswith("Pause automatique:")):
            recovered, _reason, _health = _legacy_delivery_health(conn)
            if recovered:
                conn.run("""UPDATE marketing_settings SET legacy_campaign_enabled=TRUE,
                    legacy_paused_reason='',updated_at=NOW() WHERE id=1""")
                legacy_enabled = True
                legacy_paused_reason = ""
                _notify_marketing_problem(
                    conn, notify_admin, "legacy-recovered",
                    "La santé d'envoi est revenue à un niveau normal. La campagne des anciens prospects reprend automatiquement.",
                    cooldown_hours=24,
                )
        if not dry_run:
            conn.run("""UPDATE marketing_settings SET last_run_at=NOW(),updated_at=NOW()
                WHERE id=1""")
        hour = datetime.now(PARIS_TZ).hour
        if not force and not dry_run and not 9 <= hour < 20:
            return {"ok": True, "outside_window": True, "sent": 0, "candidates": []}
        sent_today = int(conn.run("""SELECT COUNT(*) FROM marketing_email_log
            WHERE status='sent' AND sent_at>=CURRENT_DATE""")[0][0] or 0)
        remaining = max(0, min(int(batch_limit), int(daily_limit) - sent_today))
        legacy_sent_today = int(conn.run("""SELECT COUNT(*) FROM marketing_email_log
            WHERE journey='legacy_reactivation' AND status='sent'
              AND sent_at>=CURRENT_DATE""")[0][0] or 0)
        legacy_remaining = 0
        if legacy_enabled:
            healthy, pause_reason, _health = _legacy_delivery_health(conn)
            if not healthy:
                conn.run("""UPDATE marketing_settings SET legacy_campaign_enabled=FALSE,
                    legacy_paused_reason=:reason,updated_at=NOW() WHERE id=1""",
                    reason=pause_reason)
                _notify_marketing_problem(
                    conn, notify_admin, "legacy-deliverability",
                    "La campagne des anciens prospects a été suspendue automatiquement. " + pause_reason,
                    cooldown_hours=24,
                )
                legacy_enabled = False
                legacy_paused_reason = pause_reason
            else:
                start = legacy_started_at or _now()
                ramp_day = max(0, (_now().date() - start.date()).days)
                ramp_limit = 300 if ramp_day == 0 else 700 if ramp_day == 1 else int(legacy_daily_limit)
                legacy_remaining = max(0, min(int(legacy_daily_limit), ramp_limit) - legacy_sent_today)
        contacts = conn.run("""SELECT mc.member_code,mc.email,mc.first_name,mc.segment,
            COALESCE(m.created_at,mc.created_at),m.date_fin,m.billing_status,
            m.stripe_subscription_id,m.billing_cancel_at_period_end,m.date_souscription
            FROM marketing_contacts mc JOIN members m ON m.code=mc.member_code
            WHERE mc.unsubscribed_at IS NULL
              AND mc.email LIKE '%@%'
            ORDER BY CASE mc.segment WHEN 'suspended' THEN 0 WHEN 'active' THEN 1
                WHEN 'expiring' THEN 2 WHEN 'expired' THEN 3
                WHEN 'explorer' THEN 4 ELSE 5 END,mc.created_at""")
        legacy_rows = conn.run("""SELECT 'LEAD-'||id,email,first_name,'legacy_lead',
            imported_at,NULL,'','','' FROM marketing_legacy_leads
            WHERE status='active' AND unsubscribed_at IS NULL
            ORDER BY imported_at""")
        pending_rows = conn.run("""SELECT 'PENDING-'||SUBSTRING(MD5(LOWER(email)),1,16),
            LOWER(email),prenom,'pending_optin',created_at,NULL,'','',''
            FROM prospect_email_verifications
            WHERE source='explorer' AND status='pending' AND expires_at>NOW()
            ORDER BY created_at""")
        # Une confirmation Explorer en attente doit toujours passer avant une
        # ancienne séquence de reconquête portant sur la même adresse.
        contacts = list(contacts) + list(pending_rows) + list(legacy_rows)
        candidates = []
        seen_emails = set()
        legacy_selected = 0
        for contact in contacts:
            if len(candidates) >= (int(batch_limit) if dry_run else remaining):
                break
            normalized_email = str(contact[1] or "").strip().lower()
            if not normalized_email or normalized_email in seen_emails:
                continue
            seen_emails.add(normalized_email)
            # Un statut actif ou suspendu sur une adresse protège cette personne
            # de toute relance émise depuis un ancien code BCT dupliqué.
            if contact[3] == "suspended":
                continue
            if contact[3] == "legacy_lead":
                if not legacy_enabled or legacy_selected >= legacy_remaining:
                    continue
            if not _contact_rate_allowed(conn, contact[0], int(weekly_limit), int(min_gap)):
                continue
            candidate = _checkout_candidate(conn, contact)
            if not candidate:
                candidate = _renewal_candidate(conn, contact)
            if not candidate:
                candidate = _member_onboarding_candidate(conn, contact)
            if not candidate:
                candidate = _explorer_candidate(conn, contact)
            if not candidate:
                candidate = _legacy_lead_candidate(conn, contact)
            if not candidate:
                candidate = _pending_optin_candidate(conn, contact)
            if candidate:
                journey, content, reference, due_at = candidate
                candidates.append((contact, journey, content, reference, due_at))
                if journey == "legacy_reactivation":
                    legacy_selected += 1
        if dry_run:
            return {"ok": True, "dry_run": True, "sent": 0,
                    "candidates": [{"member_code": c[0], "segment": c[3],
                                    "journey": j, "stage": content["stage"],
                                    "due_at": due.isoformat() if due else None}
                                   for c, j, content, _reference, due in candidates]}
    finally:
        conn.close()

    sent = 0
    failed = 0
    results = []
    failure_errors = {}
    for contact, journey, content, reference, due_at in candidates:
        code, email, first_name = contact[0], contact[1], contact[2]
        conn = get_conn()
        try:
            claimed = _claim_email(conn, contact, journey, content, reference, due_at)
        finally:
            conn.close()
        if not claimed:
            continue
        log_id = int(claimed[0][0])
        unsubscribe_token = action_token(
            "marketing_preferences", {
                "member_code": code if not code.startswith("LEAD-") else "",
                "lead_id": int(code[5:]) if code.startswith("LEAD-") else None,
                "pending_email": email if code.startswith("PENDING-") else "",
                "email": email,
            },
            lifetime_seconds=60 * 60 * 24 * 730,
        )
        unsubscribe_url = f"{BASE_URL}/email/preferences/{unsubscribe_token}"
        subject = _personalized_subject(content["subject"], first_name)
        body = _email_html(first_name, content, journey, content["stage"], unsubscribe_url)
        result = send_email(email, first_name or "Membre Bectanse", subject, body,
                            f"marketing-{journey}-{content['stage']}")
        conn = get_conn()
        try:
            if result.get("ok"):
                conn.run("""UPDATE marketing_email_log SET status='sent',sent_at=NOW(),
                    provider_message_id=:message,error='' WHERE id=:id""",
                    message=str(result.get("message_id", ""))[:250], id=log_id)
                sent += 1
                if code.startswith("LEAD-"):
                    conn.run("""UPDATE marketing_legacy_leads SET last_contact_at=NOW()
                        WHERE id=:id""", id=int(code[5:]))
            else:
                error_text = str(result.get("error", "Erreur d'envoi"))[:500]
                conn.run("""UPDATE marketing_email_log SET status='failed',error=:error
                    WHERE id=:id""", error=error_text, id=log_id)
                failure_errors[error_text] = failure_errors.get(error_text, 0) + 1
                failed += 1
        finally:
            conn.close()
        results.append({"member_code": code, "journey": journey,
                        "stage": content["stage"], "ok": bool(result.get("ok"))})
    conn = get_conn()
    try:
        error_summary = "; ".join(
            f"{count}× {error}" for error, count in list(failure_errors.items())[:3]
        )
        conn.run("""UPDATE marketing_settings SET last_success_at=NOW(),
            last_error=:error,last_sent_count=:sent,updated_at=NOW() WHERE id=1""",
            error=error_summary[:1000], sent=sent)
        if failed:
            _notify_marketing_problem(
                conn, notify_admin, "delivery-failed",
                f"{failed} e-mail(s) n'ont pas pu être envoyés pendant le dernier passage. {error_summary}",
                cooldown_hours=6,
            )
    finally:
        conn.close()
    return {"ok": True, "sent": sent, "failed": failed, "candidates": results}


def _marketing_dashboard_data(conn):
    ensure_marketing_schema(conn)
    sync_marketing_segments(conn)
    segment_rows = conn.run("""SELECT segment,COUNT(*) FROM marketing_contacts
        GROUP BY segment ORDER BY segment""")
    segments = {str(row[0]): int(row[1]) for row in segment_rows}
    settings = conn.run("""SELECT enabled,daily_send_limit,weekly_contact_limit,
        min_gap_hours,batch_limit,updated_at,legacy_campaign_enabled,
        legacy_daily_limit,legacy_started_at,legacy_paused_reason,
        last_run_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Paris',
        last_success_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Paris',
        last_error,last_sent_count
        FROM marketing_settings WHERE id=1""")[0]
    stats = {
        "sent_24h": int(conn.run("""SELECT COUNT(*) FROM marketing_email_log
            WHERE status='sent' AND sent_at>NOW()-INTERVAL '24 hours'""")[0][0] or 0),
        "sent_7d": int(conn.run("""SELECT COUNT(*) FROM marketing_email_log
            WHERE status='sent' AND sent_at>NOW()-INTERVAL '7 days'""")[0][0] or 0),
        "failed_7d": int(conn.run("""SELECT COUNT(*) FROM marketing_email_log
            WHERE status='failed' AND created_at>NOW()-INTERVAL '7 days'""")[0][0] or 0),
        "open_checkouts": int(conn.run("""SELECT COUNT(*) FROM marketing_checkout_intents
            WHERE status='open'""")[0][0] or 0),
        "converted_checkouts": int(conn.run("""SELECT COUNT(*) FROM marketing_checkout_intents
            WHERE status='converted'""")[0][0] or 0),
        "unsubscribed": int(conn.run("""SELECT COUNT(*) FROM marketing_contacts
            WHERE unsubscribed_at IS NOT NULL""")[0][0] or 0),
        "legacy_leads": int(conn.run("""SELECT COUNT(*) FROM marketing_legacy_leads
            WHERE status='active' AND unsubscribed_at IS NULL""")[0][0] or 0),
        "legacy_clicked": int(conn.run("""SELECT COUNT(*) FROM marketing_legacy_leads
            WHERE clicked_at IS NOT NULL AND unsubscribed_at IS NULL""")[0][0] or 0),
        "legacy_suppressed": int(conn.run("""SELECT COUNT(*) FROM marketing_legacy_leads
            WHERE status='suppressed' OR unsubscribed_at IS NOT NULL""")[0][0] or 0),
        "hard_bounces_24h": int(conn.run("""SELECT COUNT(*) FROM marketing_email_events
            WHERE event_type IN ('hardbounce','invalid')
              AND event_at>NOW()-INTERVAL '24 hours'""")[0][0] or 0),
        "clicks_24h": int(conn.run("""SELECT COUNT(*) FROM marketing_email_events
            WHERE event_type='click' AND event_at>NOW()-INTERVAL '24 hours'""")[0][0] or 0),
    }
    recent = conn.run("""SELECT l.sent_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Paris',
        l.member_code,c.first_name,l.journey,l.stage,
        l.status,l.error FROM marketing_email_log l
        LEFT JOIN marketing_contacts c ON c.member_code=l.member_code
        ORDER BY l.created_at DESC LIMIT 40""")
    return segments, settings, stats, recent


def _normalize_brevo_event(value):
    key = re.sub(r"[^a-z]", "", str(value or "").lower())
    aliases = {
        "hardbounce": "hardbounce", "softbounce": "softbounce",
        "delivered": "delivered", "click": "click", "clicked": "click",
        "spam": "spam", "complaint": "spam", "invalid": "invalid",
        "blocked": "blocked", "unsubscribed": "unsubscribed",
        "opened": "opened", "uniqueopened": "opened", "deferred": "deferred",
        "sent": "sent", "request": "sent",
    }
    return aliases.get(key, key[:40])


def _brevo_tags(payload):
    tags = payload.get("tags", payload.get("tag", []))
    if isinstance(tags, str):
        try:
            parsed = json.loads(tags)
            tags = parsed if isinstance(parsed, list) else [tags]
        except Exception:
            tags = [tags]
    return [str(tag)[:160] for tag in (tags or [])]


def _record_brevo_events(conn, payload):
    events = payload if isinstance(payload, list) else [payload]
    accepted = 0
    for item in events:
        if not isinstance(item, dict):
            continue
        tags = _brevo_tags(item)
        if not any(tag.startswith("marketing-") for tag in tags):
            continue
        email = str(item.get("email") or item.get("to") or "").strip().lower()[:254]
        if "@" not in email:
            continue
        event_type = _normalize_brevo_event(item.get("event") or item.get("type"))
        if not event_type:
            continue
        provider_id = str(item.get("message-id") or item.get("messageId") or "")[:250]
        canonical = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
        event_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        log_rows = conn.run("""SELECT id,journey,stage FROM marketing_email_log
            WHERE LOWER(recipient_email)=LOWER(:email)
            ORDER BY CASE WHEN provider_message_id<>'' AND provider_message_id=:provider THEN 0 ELSE 1 END,
                created_at DESC LIMIT 1""", email=email, provider=provider_id)
        journey = str(log_rows[0][1]) if log_rows else ""
        stage = str(log_rows[0][2]) if log_rows else ""
        inserted = conn.run("""INSERT INTO marketing_email_events
            (event_hash,recipient_email,event_type,provider_message_id,journey,stage,payload)
            VALUES (:hash,:email,:event,:provider,:journey,:stage,:payload)
            ON CONFLICT (event_hash) DO NOTHING RETURNING id""",
            hash=event_hash, email=email, event=event_type, provider=provider_id,
            journey=journey, stage=stage, payload=canonical[:4000])
        if not inserted:
            continue
        accepted += 1
        if log_rows:
            log_id = int(log_rows[0][0])
            if event_type == "delivered":
                conn.run("""UPDATE marketing_email_log SET provider_status='delivered',
                    delivered_at=COALESCE(delivered_at,NOW()) WHERE id=:id""", id=log_id)
            elif event_type == "click":
                conn.run("""UPDATE marketing_email_log SET provider_status='clicked',
                    clicked_at=COALESCE(clicked_at,NOW()) WHERE id=:id""", id=log_id)
            elif event_type in {"hardbounce", "softbounce", "invalid", "blocked", "spam", "unsubscribed"}:
                conn.run("""UPDATE marketing_email_log SET provider_status=:status
                    WHERE id=:id""", status=event_type, id=log_id)
        if event_type == "delivered":
            conn.run("""UPDATE marketing_legacy_leads SET delivered_at=COALESCE(delivered_at,NOW())
                WHERE LOWER(email)=LOWER(:email)""", email=email)
        elif event_type == "click":
            conn.run("""UPDATE marketing_legacy_leads SET clicked_at=COALESCE(clicked_at,NOW())
                WHERE LOWER(email)=LOWER(:email) AND status='active'""", email=email)
        elif event_type in {"hardbounce", "invalid", "blocked", "spam", "unsubscribed"}:
            conn.run("""UPDATE marketing_legacy_leads SET status='suppressed',
                unsubscribed_at=COALESCE(unsubscribed_at,NOW()),bounced_at=CASE
                    WHEN :event IN ('hardbounce','invalid','blocked') THEN COALESCE(bounced_at,NOW())
                    ELSE bounced_at END WHERE LOWER(email)=LOWER(:email)""",
                event=event_type, email=email)
            conn.run("""UPDATE marketing_contacts SET unsubscribed_at=COALESCE(unsubscribed_at,NOW()),
                updated_at=NOW() WHERE LOWER(email)=LOWER(:email)""", email=email)
    return accepted


def register_marketing_routes(app, get_conn, send_email, action_token, action_payload,
                              admin_required, notify_admin=None):
    @app.route("/webhooks/brevo/marketing", methods=["POST"])
    def brevo_marketing_webhook():
        secret = os.environ.get("BREVO_WEBHOOK_SECRET", "")
        authorization = request.headers.get("Authorization", "")
        if not secret or not hmac.compare_digest(authorization, "Bearer " + secret):
            return jsonify({"ok": False}), 403
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"ok": False, "error": "JSON invalide"}), 400
        conn = get_conn()
        try:
            ensure_marketing_schema(conn)
            accepted = _record_brevo_events(conn, payload)
        finally:
            conn.close()
        return jsonify({"ok": True, "accepted": accepted})

    @app.route("/admin/marketing")
    @admin_required
    def admin_marketing():
        conn = get_conn()
        try:
            segments, settings, stats, recent = _marketing_dashboard_data(conn)
        finally:
            conn.close()
        return render_template("admin_marketing.html", segments=segments,
            settings=settings, stats=stats, recent=recent,
            explorer_stages=EXPLORER_STAGES, checkout_stages=CHECKOUT_STAGES,
            reactivation_stages=REACTIVATION_STAGES,
            member_stages=MEMBER_ONBOARDING_STAGES)

    @app.route("/admin/marketing/run", methods=["POST"])
    @admin_required
    def admin_marketing_run():
        data = request.get_json(silent=True) or {}
        result = run_marketing_automation(
            get_conn, send_email, action_token,
            dry_run=bool(data.get("dry_run", True)), force=bool(data.get("force", False)),
            notify_admin=notify_admin)
        return jsonify(result)

    @app.route("/admin/marketing/toggle", methods=["POST"])
    @admin_required
    def admin_marketing_toggle():
        data = request.get_json(silent=True) or {}
        enabled = data.get("enabled") is True
        conn = get_conn()
        try:
            ensure_marketing_schema(conn)
            conn.run("""UPDATE marketing_settings SET enabled=:enabled,updated_at=NOW()
                WHERE id=1""", enabled=enabled)
        finally:
            conn.close()
        return jsonify({"ok": True, "enabled": enabled})

    @app.route("/admin/marketing/legacy-toggle", methods=["POST"])
    @admin_required
    def admin_marketing_legacy_toggle():
        data = request.get_json(silent=True) or {}
        enabled = data.get("enabled") is True
        conn = get_conn()
        try:
            ensure_marketing_schema(conn)
            conn.run("""UPDATE marketing_settings SET legacy_campaign_enabled=:enabled,
                legacy_started_at=CASE WHEN :enabled AND legacy_started_at IS NULL
                    THEN NOW() ELSE legacy_started_at END,
                legacy_paused_reason=CASE WHEN :enabled THEN '' ELSE 'Pause manuelle' END,
                updated_at=NOW() WHERE id=1""", enabled=enabled)
        finally:
            conn.close()
        return jsonify({"ok": True, "legacy_campaign_enabled": enabled})

    @app.route("/admin/marketing/import-leads", methods=["POST"])
    @admin_required
    def admin_marketing_import_leads():
        upload = request.files.get("file")
        if not upload:
            return jsonify({"ok": False, "error": "Fichier CSV manquant"}), 400
        try:
            text = upload.stream.read(2 * 1024 * 1024).decode("utf-8-sig")
        except Exception:
            return jsonify({"ok": False, "error": "Le fichier doit être un CSV UTF-8"}), 400
        sample = text[:4000]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except Exception:
            dialect = csv.excel
            dialect.delimiter = ";"
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        imported = duplicate = invalid = 0
        conn = get_conn()
        try:
            ensure_marketing_schema(conn)
            for raw in reader:
                normalized = {str(k or "").strip().lower(): str(v or "").strip() for k, v in raw.items()}
                email = (normalized.get("email") or normalized.get("e-mail") or
                         normalized.get("mail") or normalized.get("adresse email") or "").lower()[:254]
                first = (normalized.get("prenom") or normalized.get("prénom") or
                         normalized.get("first_name") or normalized.get("nom") or "")[:80]
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                    invalid += 1
                    continue
                rows = conn.run("""INSERT INTO marketing_legacy_leads
                    (email,first_name,source,status) VALUES (:email,:first,'anciens-prospects-2025','active')
                    ON CONFLICT (LOWER(email)) DO NOTHING RETURNING id""", email=email, first=first)
                if rows:
                    imported += 1
                else:
                    duplicate += 1
            if imported:
                conn.run("""UPDATE marketing_settings SET enabled=TRUE,
                    legacy_campaign_enabled=TRUE,
                    legacy_started_at=COALESCE(legacy_started_at,NOW()),
                    legacy_paused_reason='',updated_at=NOW() WHERE id=1""")
        finally:
            conn.close()
        return jsonify({"ok": True, "imported": imported, "duplicates": duplicate, "invalid": invalid})

    @app.route("/email/preferences/<token>", methods=["GET", "POST"])
    def marketing_preferences(token):
        payload = action_payload(token, "marketing_preferences")
        if not payload:
            return render_template("email_preferences.html", invalid=True,
                                   unsubscribed=False, email=""), 400
        code = str(payload.get("member_code", ""))[:80]
        lead_id = payload.get("lead_id")
        pending_email = str(payload.get("pending_email", "")).strip().lower()[:254]
        email = str(payload.get("email", "")).strip().lower()[:254]
        conn = get_conn()
        try:
            ensure_marketing_schema(conn)
            if pending_email:
                rows = conn.run("""SELECT CASE WHEN status='declined' THEN NOW() ELSE NULL END
                    FROM prospect_email_verifications WHERE LOWER(email)=LOWER(:email)
                      AND source='explorer'""", email=pending_email)
            elif lead_id:
                rows = conn.run("""SELECT unsubscribed_at FROM marketing_legacy_leads
                    WHERE id=:id AND LOWER(email)=LOWER(:email)""", id=int(lead_id), email=email)
            else:
                rows = conn.run("""SELECT unsubscribed_at FROM marketing_contacts
                    WHERE member_code=:code AND LOWER(email)=LOWER(:email)""", code=code, email=email)
            if not rows:
                return render_template("email_preferences.html", invalid=True,
                                       unsubscribed=False, email=""), 404
            if request.method == "POST":
                if pending_email:
                    conn.run("""UPDATE prospect_email_verifications SET status='declined'
                        WHERE LOWER(email)=LOWER(:email) AND source='explorer' AND status='pending'""",
                        email=pending_email)
                elif lead_id:
                    conn.run("""UPDATE marketing_legacy_leads SET unsubscribed_at=NOW(),
                        status='unsubscribed' WHERE id=:id AND LOWER(email)=LOWER(:email)""",
                        id=int(lead_id), email=email)
                else:
                    conn.run("""UPDATE marketing_contacts SET unsubscribed_at=NOW(),updated_at=NOW()
                        WHERE member_code=:code AND LOWER(email)=LOWER(:email)""", code=code, email=email)
                return redirect(url_for("marketing_preferences", token=token, done="1"))
            unsubscribed = bool(rows[0][0]) or request.args.get("done") == "1"
        finally:
            conn.close()
        return render_template("email_preferences.html", invalid=False,
                               unsubscribed=unsubscribed, email=email)
