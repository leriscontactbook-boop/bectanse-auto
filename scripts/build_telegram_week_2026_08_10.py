"""Construit le planning éditorial Bectanse du 10 au 16 août 2026."""

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "content" / "planning_telegram_2026-08-10_au_2026-08-16.csv"
PLAN_PATH = ROOT / "content" / "planning_telegram_2026-08-10_au_2026-08-16.md"
SITE = "https://acces.bectanse-academie.com/"
CHANNEL = "@BECTANSE_ACADEMIE"
VISUAL_BASE = f"{SITE.rstrip('/')}/static/telegram-visuals"
VISUALS = {
    "london": f"{VISUAL_BASE}/01-session-londres-ouverte-v2.webp",
    "us": f"{VISUAL_BASE}/02-session-americaine-t30-v2.webp",
    "economic": f"{VISUAL_BASE}/03-annonce-economique-majeure-v2.webp",
    "result": f"{VISUAL_BASE}/04-resultat-de-la-journee-v2.webp",
    "quiz": f"{VISUAL_BASE}/05-quiz-du-marche-v2.webp",
    "testimonial": f"{VISUAL_BASE}/06-nouveau-temoignage-v2.webp",
    "alert": f"{VISUAL_BASE}/07-derniere-alerte-disponible-v2.webp",
}

CSV_COLUMNS = [
    "nom", "type", "date", "heure", "rythme", "jours", "semaine_rotation",
    "canal", "message", "image_url", "texte_bouton", "lien_bouton",
    "question", "reponses", "bonnes_reponses", "explication", "anonyme",
    "choix_multiples", "silencieux", "actif", "tous_les_canaux", "canaux"
]

posts = []


def add(day, date, time, name, objective, angle, message="", *, post_type="message",
        button_text="", question="", options=None, correct=None, explanation="",
        multiple=False, silent=False, include_csv=True):
    posts.append({
        "day": day,
        "date": date,
        "time": time,
        "name": name,
        "objective": objective,
        "angle": angle,
        "message": message.strip(),
        "image_url": "",
        "post_type": post_type,
        "button_text": button_text,
        "question": question.strip(),
        "options": options or [],
        "correct": correct or [],
        "explanation": explanation.strip(),
        "multiple": multiple,
        "silent": silent,
        "include_csv": include_csv,
    })


def add_live_agenda(day, date, verified_note):
    add(
        day, date, "08:30", "Agenda économique vérifié",
        "Informer sans inventer et renforcer la gestion du risque.",
        "Publication dynamique : événements à impact fort ou moyen, heures de Paris.",
        (
            "📅 *AGENDA ÉCONOMIQUE DU JOUR*\n\n"
            "Le robot compose ce message à 08:30 avec les événements réellement disponibles "
            "dans la source du jour.\n\n"
            f"Repère vérifié pour la stratégie : {verified_note}\n\n"
            "_Le calendrier informe sur le timing, jamais sur la direction future du marché._"
        ),
        include_csv=False,
    )


# LUNDI — énergie, discipline, passage à l'action
add("LUNDI", "10/08/2026", "00:00", "La semaine commence maintenant",
    "Créer le rendez-vous et lancer la progression psychologique de la semaine.",
    "Nouvelle semaine, responsabilité personnelle.",
    """🌙 *LA SEMAINE COMMENCE MAINTENANT*

Les écrans peuvent attendre quelques heures. Ton plan, lui, ne doit pas attendre lundi matin.

Fixe ton risque. Note tes priorités. Repose-toi.

À 09:00, on se retrouve avec une seule mission : exécuter avec discipline.

Bonne nuit l’équipe. 🔥""", silent=True)
add_live_agenda("LUNDI", "10/08/2026", "aucune statistique américaine majeure du BLS n’est programmée ce lundi dans le calendrier officiel consulté.")
add("LUNDI", "10/08/2026", "09:00", "Ouverture Londres — cap sur le plan",
    "Créer de l’énergie et faire revenir la communauté.",
    "La discipline avant la recherche d’opportunité.",
    """🇬🇧 *LONDRES EST LANCÉE*

Nouvelle semaine. Nouveau terrain de jeu. Même règle : pas d’entrée sans scénario.

Observe d’abord. Décide ensuite.

Qui est déjà devant ses graphiques ? 🔥""")
add("LUNDI", "10/08/2026", "10:00", "Quiz discipline du lundi",
    "Créer un premier engagement simple et installer l’identité du trader sérieux.",
    "La répétition disciplinée bat la recherche du coup parfait.",
    post_type="quiz",
    question="Qu’est-ce qui construit le plus une performance durable en trading ?",
    options=["La chance", "Une discipline répétée", "Le signal parfait", "Trader plus souvent"],
    correct=[2],
    explanation="La discipline permet d’appliquer le même processus, de mesurer ses décisions et de protéger le capital.")
add("LUNDI", "10/08/2026", "11:00", "Le point d’analyse du lundi",
    "Éduquer puis générer un clic qualifié.",
    "Une analyse sert à préparer des scénarios, pas à prédire.",
    """📊 *TON ANALYSE DOIT RÉPONDRE À 3 QUESTIONS*

1. Où le scénario devient-il intéressant ?
2. Où devient-il invalide ?
3. Quel risque es-tu prêt à accepter ?

Retrouve les mises à jour disponibles dans ton espace et prépare ton plan avant d’agir.

_Le trading comporte un risque de perte. Aucun résultat n’est garanti._""",
    button_text="VOIR L’ANALYSE")
add("LUNDI", "10/08/2026", "12:00", "Fenêtre des premiers signaux",
    "Créer de l’attente sans promettre une opportunité.",
    "Un signal ne vaut que si ses conditions sont réunies.",
    """🚨 *LA FENÊTRE DE SURVEILLANCE APPROCHE*

Les premiers scénarios de la journée sont étudiés.

S’il n’y a pas de configuration propre, on ne force rien. S’il y en a une, les membres en place la verront au bon moment.

Prépare ton risque avant l’alerte, pas après.""",
    button_text="REJOINDRE LA SESSION")
add("LUNDI", "10/08/2026", "14:00", "Pré-session US du lundi",
    "Relancer l’attention avant la période américaine.",
    "Nouvelle liquidité, même discipline.",
    """🇺🇸 *LE DESK US SE PRÉPARE*

La deuxième partie de journée peut changer le rythme du marché.

Pas besoin d’anticiper chaque mouvement : prépare deux scénarios et accepte aussi celui où tu ne trades pas.

Rendez-vous à 15:00. 🔥""")
add("LUNDI", "10/08/2026", "15:00", "Session US Bectanse — lundi",
    "Créer un rendez-vous communautaire.",
    "Concentration collective et exécution individuelle.",
    """🇺🇸 *SESSION US BECTANSE OUVERTE*

Le plan est posé. Le risque est défini. Maintenant, on laisse le marché confirmer — ou invalider.

Ne cours pas après le prix.

L’équipe est en place. 🎯""")
add("LUNDI", "10/08/2026", "16:00", "Vérification des alertes — lundi",
    "Générer un clic utile au moment où l’audience est attentive.",
    "Vérifier les alertes sans inventer leur existence.",
    """🔔 *POINT ALERTES*

Une configuration propre respecte le plan, le niveau d’invalidation et le risque prévu.

Consulte ton espace pour vérifier si une nouvelle alerte répond aujourd’hui à ces critères.

Pas d’alerte ? Pas de trade forcé.""",
    button_text="VÉRIFIER LES ALERTES")
add("LUNDI", "10/08/2026", "20:30", "Le choix du lundi soir",
    "Convertir les membres qui repoussent leur passage à l’action.",
    "On ne change pas sa semaine en restant spectateur.",
    """🌙 *TA SEMAINE NE SE DÉCIDE PAS VENDREDI*

Elle se décide dans les habitudes que tu poses maintenant.

Si tu veux une structure, des analyses et un espace pour suivre les alertes, tu peux commencer ce soir.

Demain, on continue. Mais ton premier pas peut être maintenant.

_Le trading comporte un risque de perte. Aucun gain n’est garanti._""",
    button_text="COMMENCER MAINTENANT")


# MARDI — éducation et gestion du risque
add("MARDI", "11/08/2026", "00:00", "Clôture du lundi, préparation du mardi",
    "Créer la continuité quotidienne.",
    "Transformer la journée en apprentissage.",
    """🌙 *LE MARCHÉ S’ARRÊTE, L’APPRENTISSAGE RESTE*

Avant de fermer : note une décision correcte et une erreur à ne pas répéter.

Demain, on travaille la gestion du risque — le sujet que beaucoup ignorent jusqu’au jour où il est trop tard.

Rendez-vous à 09:00. Bonne nuit l’équipe.""", silent=True)
add_live_agenda("MARDI", "11/08/2026", "le robot vérifiera le calendrier complet du jour à l’heure de publication.")
add("MARDI", "11/08/2026", "09:00", "Londres — mardi pédagogique",
    "Réactiver l’audience avec une règle concrète.",
    "La taille de position vient après le risque accepté.",
    """🇬🇧 *SESSION LONDRES*

Question avant toute entrée : combien peux-tu perdre sans modifier ton comportement ?

Si tu ne connais pas la réponse, la taille de ta position n’est pas encore définie correctement.

On observe. On calcule. Puis seulement, on décide.""")
add("MARDI", "11/08/2026", "10:00", "Quiz ratio risque/rendement",
    "Éduquer grâce à une interaction native.",
    "Un calcul simple qui filtre les scénarios déséquilibrés.",
    post_type="quiz",
    question="Tu risques 50 € pour viser un gain potentiel de 100 €. Quel est le ratio risque/rendement ?",
    options=["1:1", "1:2", "2:1", "1:3"],
    correct=[2],
    explanation="Tu risques une unité pour un gain potentiel de deux unités : le ratio est 1:2. Cela ne garantit pas le résultat.")
add("MARDI", "11/08/2026", "11:00", "Analyse — risque avant direction",
    "Démontrer l’expertise et générer un clic qualifié.",
    "Commencer l’analyse par l’invalidation.",
    """📐 *L’ENTRÉE N’EST PAS LA PREMIÈRE QUESTION*

Commence par le niveau qui invalide ton idée. Il détermine ton risque, ta taille et parfois la décision de ne pas intervenir.

Les éléments disponibles aujourd’hui sont regroupés dans ton espace.

_Contenu éducatif. Le trading comporte un risque de perte._""",
    button_text="ACCÉDER À L’ANALYSE")
add("MARDI", "11/08/2026", "12:00", "Préparation des scénarios — mardi",
    "Créer de l’attente tout en renforçant la discipline.",
    "Préparer l’invalidation avant l’alerte.",
    """⏱ *AVANT LES PREMIERS SCÉNARIOS*

Vérifie trois choses : ton risque maximal, ton niveau d’invalidation et le nombre de positions déjà ouvertes.

Une alerte n’efface jamais les règles de gestion du risque.

Les membres peuvent rejoindre l’espace avant la prochaine mise à jour.""",
    button_text="PRÉPARER LA SESSION")
add("MARDI", "11/08/2026", "14:00", "Pré-session US — mardi",
    "Maintenir le rendez-vous de l’après-midi.",
    "La volatilité n’est pas une obligation de trader.",
    """🇺🇸 *LA PÉRIODE US APPROCHE*

Plus de mouvement ne signifie pas automatiquement plus de qualité.

Attends ton contexte. Refuse les entrées improvisées. Garde la même taille de risque.

On se retrouve à 15:00.""")
add("MARDI", "11/08/2026", "15:00", "Session US Bectanse — mardi",
    "Créer de l’appartenance et rappeler le processus.",
    "Lire, attendre, exécuter seulement si le scénario est valide.",
    """🇺🇸 *LE DESK US EST EN PLACE*

Lecture du contexte.
Attente de la confirmation.
Risque défini.

Trois étapes simples. Le plus difficile est de ne pas les raccourcir.

Reste concentré. 🔥""")
add("MARDI", "11/08/2026", "16:00", "Alertes et taille de position",
    "Faire cliquer avec une raison pédagogique.",
    "Consulter l’alerte puis calculer, jamais l’inverse.",
    """🚨 *UNE ALERTE N’EST PAS UNE TAILLE DE POSITION*

Deux traders peuvent observer le même scénario et prendre un risque différent selon leur capital et leur plan.

Vérifie les dernières mises à jour, puis applique ta propre limite de risque.""",
    button_text="VOIR LES MISES À JOUR")
add("MARDI", "11/08/2026", "20:30", "L’information ne suffit pas",
    "Faire prendre conscience du besoin de structure.",
    "Le problème n’est pas toujours le manque de contenu.",
    """🧠 *TU NE MANQUES PEUT-ÊTRE PAS D’INFORMATIONS*

Tu manques peut-être d’un processus assez simple pour être répété quand l’émotion monte.

Une structure utile t’aide à préparer, filtrer et revoir tes décisions.

Si c’est ce qu’il te manque, découvre l’écosystème Bectanse.

_Aucun résultat financier n’est garanti._""",
    button_text="DÉCOUVRIR L’ESPACE")


# MERCREDI — psychologie, FOMO, journée CPI
add("MERCREDI", "12/08/2026", "00:00", "Mercredi sous contrôle",
    "Préparer psychologiquement une journée macro importante.",
    "La patience protège avant une annonce.",
    """🌙 *DEMAIN, LA PATIENCE SERA UN AVANTAGE*

Une statistique d’inflation américaine est programmée à 14:30, heure de Paris.

Ne prépare pas une prédiction. Prépare ta réaction : risque réduit, scénario clair, aucune poursuite impulsive.

Repose-toi. On se retrouve à 09:00.""", silent=True)
add_live_agenda("MERCREDI", "12/08/2026", "14:30 — IPC américain de juillet et salaires réels, selon le calendrier du Bureau of Labor Statistics.")
add("MERCREDI", "12/08/2026", "09:00", "Londres — mercredi de patience",
    "Créer un rendez-vous et prévenir le FOMO.",
    "Une journée chargée ne se trade pas dès la première bougie.",
    """🇬🇧 *LONDRES EST OUVERTE*

Le vrai défi aujourd’hui ne sera peut-être pas de trouver une entrée.

Ce sera d’attendre quand les conditions ne sont pas encore réunies.

L’IPC américain est attendu à 14:30. Garde du capital mental pour l’après-midi.""")
add("MERCREDI", "12/08/2026", "10:00", "Quiz anti-FOMO",
    "Engager et apprendre à interrompre une réaction émotionnelle.",
    "Rater un mouvement coûte moins cher que poursuivre sans plan.",
    post_type="quiz",
    question="Le prix part sans toi et l’envie de courir après le mouvement monte. Quelle est la meilleure première action ?",
    options=["Entrer immédiatement", "Revenir au plan et attendre un nouveau scénario", "Doubler la taille", "Supprimer le stop"],
    correct=[2],
    explanation="Revenir au plan évite une décision dictée par le FOMO. Un mouvement raté n’oblige jamais à improviser.")
add("MERCREDI", "12/08/2026", "11:00", "Analyse avant l’IPC",
    "Apporter de la valeur et envoyer vers l’espace.",
    "Cartographier les niveaux sans prédire la statistique.",
    """📊 *AVANT 14:30 : CARTOGRAPHIE, PAS PRÉDICTION*

Identifie les niveaux où la liquidité peut se concentrer. Prépare un scénario haussier, un scénario baissier et une zone où tu refuses d’agir.

Retrouve les éléments de préparation disponibles dans ton espace.

_Le trading comporte un risque de perte._""",
    button_text="VOIR LA PRÉPARATION")
add("MERCREDI", "12/08/2026", "12:00", "Avant l’annonce, protéger le capital",
    "Créer de l’urgence responsable.",
    "La meilleure préparation consiste parfois à réduire l’exposition.",
    """⚠️ *AVANT LA VOLATILITÉ*

L’IPC américain est programmé à 14:30, heure de Paris.

Évite les positions prises par impatience. Vérifie ton exposition et décide maintenant de ton risque maximal.

La prochaine mise à jour sera visible dans l’espace membres.""",
    button_text="REJOINDRE LA SESSION")
add("MERCREDI", "12/08/2026", "14:00", "Compte à rebours IPC",
    "Prévenir l’audience juste avant l’événement vérifié.",
    "30 minutes avant l’IPC : aucune prédiction, règles renforcées.",
    """⏳ *IPC US DANS 30 MINUTES*

À 14:30, la publication de l’inflation américaine peut accélérer plusieurs marchés.

Ne confonds pas vitesse et opportunité.

Attends la donnée, observe la réaction et refuse de poursuivre une bougie sans scénario.""")
add("MERCREDI", "12/08/2026", "15:00", "Après l’IPC — revenir au plan",
    "Ramener la communauté à la discipline après la volatilité.",
    "La première réaction n’est pas toujours un signal exploitable.",
    """🇺🇸 *SESSION US BECTANSE*

La statistique est sortie. Maintenant, oublie l’envie d’avoir raison.

Observe ce que le prix confirme réellement. Si ton niveau est déjà loin, laisse partir le mouvement.

Protéger son capital, c’est aussi savoir ne pas poursuivre.""")
add("MERCREDI", "12/08/2026", "16:00", "Alertes après volatilité",
    "Créer un clic contextualisé sans prétendre qu’un signal existe.",
    "Vérifier si un scénario propre s’est formé après l’annonce.",
    """🔔 *LE MARCHÉ A BOUGÉ. LE PLAN A-T-IL CONFIRMÉ ?*

Une accélération ne suffit pas. Il faut encore un contexte, une invalidation et un risque cohérent.

Consulte l’espace pour vérifier si une alerte exploitable a été publiée après la réaction.""",
    button_text="CONSULTER LES ALERTES")
add("MERCREDI", "12/08/2026", "20:30", "Le calme est une compétence",
    "Convertir par l’identité et le mindset.",
    "Le trader structuré se distingue dans les moments rapides.",
    """🌙 *LE CALME N’EST PAS UN TRAIT DE CARACTÈRE*

C’est une compétence qui se construit avec des règles préparées avant la volatilité.

Si tu veux arrêter d’improviser seul quand le marché accélère, rejoins une structure pensée pour préparer et suivre les scénarios.

_Aucun gain n’est garanti. Le risque de perte existe._""",
    button_text="REJOINDRE L’ÉQUIPE")


# JEUDI — expertise, lecture du marché, journée PPI
add("JEUDI", "13/08/2026", "00:00", "Transition vers le jeudi technique",
    "Maintenir le rendez-vous et annoncer la montée en compétence.",
    "Après l’émotion, retour à la lecture du marché.",
    """🌙 *DEMAIN, ON MONTE D’UN NIVEAU*

Le marché ne récompense pas le vocabulaire compliqué. Il récompense les décisions cohérentes.

Jeudi, on travaille la confirmation, le rejet et l’invalidation.

Une nouvelle statistique de prix américaine est attendue à 14:30. Repose-toi et arrive avec un plan.""", silent=True)
add_live_agenda("JEUDI", "13/08/2026", "14:30 — indice américain des prix à la production de juillet, selon le Bureau of Labor Statistics.")
add("JEUDI", "13/08/2026", "09:00", "Londres — jeudi expertise",
    "Créer le rendez-vous avec un angle plus avancé.",
    "Une cassure n’existe pas seulement parce qu’une mèche dépasse un niveau.",
    """🇬🇧 *LONDRES EST EN MOUVEMENT*

Aujourd’hui, observe la manière dont le prix quitte un niveau — puis surtout la manière dont il y revient.

Une cassure sans acceptation peut devenir un piège.

Lis la clôture, le contexte et l’invalidation. Pas seulement la mèche.""")
add("JEUDI", "13/08/2026", "10:00", "Quiz lecture de cassure",
    "Faire progresser l’audience avec un quiz plus expert.",
    "Différencier cassure confirmée et rejet possible.",
    post_type="quiz",
    question="Le prix dépasse une résistance puis clôture rapidement sous le niveau. Quelle lecture est la plus rigoureuse ?",
    options=["Cassure confirmée", "Achat automatique", "Rejet possible à confirmer", "Le niveau n’a plus aucune importance"],
    correct=[3],
    explanation="Le retour sous le niveau suggère un rejet possible, mais le contexte et une confirmation restent nécessaires avant toute décision.")
add("JEUDI", "13/08/2026", "11:00", "Analyse — confirmation et invalidation",
    "Démontrer l’expertise et orienter vers l’analyse disponible.",
    "Construire un scénario falsifiable.",
    """📊 *UN BON SCÉNARIO PEUT ÊTRE INVALIDÉ*

S’il n’existe aucun niveau précis qui te prouve que ton idée est fausse, ce n’est pas encore un scénario exploitable.

Travaille aujourd’hui la confirmation, l’invalidation et le rapport risque/rendement.

Les mises à jour sont disponibles dans ton espace.""",
    button_text="OUVRIR L’ANALYSE")
add("JEUDI", "13/08/2026", "12:00", "Préparation PPI et scénarios",
    "Créer de l’attente responsable avant l’après-midi.",
    "Ne pas confondre anticipation et préparation.",
    """🚨 *PRÉPARE, NE DEVINE PAS*

Le PPI américain est programmé à 14:30, heure de Paris.

Prépare les niveaux qui comptent, puis laisse la donnée et le prix révéler le scénario.

Les membres peuvent rejoindre la session avant la prochaine mise à jour.""",
    button_text="ACCÉDER À LA SESSION")
add("JEUDI", "13/08/2026", "14:00", "Compte à rebours PPI",
    "Prévenir avant une annonce vérifiée.",
    "30 minutes avant le PPI : réduire l’impulsivité.",
    """⏳ *PPI US DANS 30 MINUTES*

La publication des prix à la production peut modifier rapidement le rythme du marché.

Pas de prédiction. Pas de position surdimensionnée. Pas de poursuite aveugle.

Observe la réaction avant d’interpréter.""")
add("JEUDI", "13/08/2026", "15:00", "Session US Bectanse — jeudi",
    "Rassembler la communauté après l’annonce.",
    "Attendre l’acceptation ou le rejet.",
    """🇺🇸 *LE DESK US EST OUVERT*

La première impulsion est passée. Le travail commence maintenant : le prix accepte-t-il les nouveaux niveaux ou les rejette-t-il ?

Ne transforme pas une réaction en certitude.

On reste méthodiques. 🎯""")
add("JEUDI", "13/08/2026", "16:00", "Vérification des opportunités — jeudi",
    "Générer un clic motivé par l’expertise.",
    "Chercher une structure confirmée, pas du mouvement brut.",
    """🔎 *MOUVEMENT OU OPPORTUNITÉ ?*

La différence se trouve dans la confirmation, l’invalidation et le risque acceptable.

Vérifie dans l’espace si une configuration répond à ces trois conditions aujourd’hui.""",
    button_text="VOIR LES OPPORTUNITÉS")
add("JEUDI", "13/08/2026", "20:30", "Monter en compétence",
    "Convertir par la progression et l’accompagnement.",
    "Passer de réactions isolées à un processus.",
    """🌙 *TU N’AS PAS BESOIN DE TOUT PRÉDIRE*

Tu as besoin d’un processus qui te dit quoi observer, quand attendre et où ton scénario devient invalide.

Si tu veux travailler avec davantage de structure et suivre les analyses de l’équipe, l’accès est ouvert.

_Le trading comporte un risque de perte._""",
    button_text="ACCÉDER À BECTANSE")


# VENDREDI — bilan, performance, projection
add("VENDREDI", "14/08/2026", "00:00", "Dernière session, même exigence",
    "Préparer le bilan sans relâcher la discipline.",
    "Vendredi n’autorise pas à rendre au marché ce qui a été protégé.",
    """🌙 *DERNIÈRE JOURNÉE, MÊME EXIGENCE*

Le vendredi pousse parfois à forcer un dernier trade pour « finir la semaine ».

Ton objectif demain n’est pas de fabriquer un résultat. C’est de respecter ton plan une dernière fois.

Rendez-vous à 09:00.""", silent=True)
add_live_agenda("VENDREDI", "14/08/2026", "14:30 — ventes au détail américaines de juillet, selon le calendrier du U.S. Census Bureau.")
add("VENDREDI", "14/08/2026", "09:00", "Londres — vendredi de maîtrise",
    "Créer de l’énergie tout en évitant le surtrading.",
    "Une semaine ne se sauve pas avec un trade forcé.",
    """🇬🇧 *LONDRES EST OUVERTE*

Dernière session de la semaine.

Si ton scénario n’est pas clair, ton meilleur résultat peut être de conserver ton capital et ta discipline.

On ne force pas une conclusion. On exécute le plan.""")
add("VENDREDI", "14/08/2026", "10:00", "Sondage bilan de la semaine",
    "Maximiser l’engagement et provoquer une introspection utile.",
    "Identifier le frein principal sans imposer une bonne réponse.",
    post_type="poll",
    question="Cette semaine, quel comportement t’a le plus freiné ?",
    options=["Entrer trop tôt", "Sortir trop tôt", "Surtrader", "Manquer de discipline"],
    multiple=False)
add("VENDREDI", "14/08/2026", "11:00", "Analyse — finir proprement",
    "Apporter une dernière valeur analytique et générer un clic.",
    "Le vendredi, réduire la pression du résultat.",
    """📊 *FINIR PROPREMENT VAUT PLUS QUE FINIR EN FORCE*

Avant la session US, vérifie ton exposition, ton risque cumulé et les décisions déjà prises cette semaine.

L’analyse du jour doit servir ton plan — pas ton envie de te refaire.

Retrouve les éléments disponibles dans ton espace.""",
    button_text="VOIR L’ANALYSE DU JOUR")
add("VENDREDI", "14/08/2026", "12:00", "Avant les ventes au détail US",
    "Créer une attente contextualisée et responsable.",
    "Préparer l’annonce sans promettre un mouvement.",
    """⚠️ *LE RENDEZ-VOUS MACRO DE L’APRÈS-MIDI*

Les ventes au détail américaines sont programmées à 14:30, heure de Paris.

La donnée peut influencer les anticipations sur l’activité, mais elle ne donne jamais à elle seule une direction certaine.

Prépare ton risque avant la publication.""",
    button_text="PRÉPARER LA SESSION")
add("VENDREDI", "14/08/2026", "14:00", "Compte à rebours ventes au détail",
    "Prévenir l’audience 30 minutes avant l’événement.",
    "Protéger la semaine avant la volatilité potentielle.",
    """⏳ *VENTES AU DÉTAIL US DANS 30 MINUTES*

Dernière statistique majeure de notre semaine éditoriale.

Ne laisse pas trente secondes de volatilité effacer cinq jours de discipline.

Réduis l’impulsivité. Observe d’abord.""")
add("VENDREDI", "14/08/2026", "15:00", "Session US Bectanse — vendredi",
    "Créer le dernier rendez-vous de marché de la semaine.",
    "La qualité de clôture compte plus que le nombre de trades.",
    """🇺🇸 *DERNIÈRE SESSION US DE LA SEMAINE*

Le marché a reçu sa donnée. Toi, garde tes règles.

Une configuration propre mérite une analyse. Une configuration moyenne mérite d’être ignorée.

Finissons la semaine avec maîtrise. 🔥""")
add("VENDREDI", "14/08/2026", "16:00", "Dernier point alertes",
    "Créer un clic sans forcer une fausse urgence.",
    "Vérifier les mises à jour avant la clôture de la semaine.",
    """🔔 *DERNIER POINT ALERTES*

Vérifie les mises à jour disponibles et assure-toi que toute décision reste compatible avec ton risque de fin de semaine.

S’il n’y a rien de propre, terminer sans nouveau trade est aussi une décision professionnelle.""",
    button_text="VÉRIFIER L’ESPACE")
add("VENDREDI", "14/08/2026", "20:30", "Le vrai résultat de la semaine",
    "Créer de la confiance et préparer le recul du week-end.",
    "Mesurer le respect du processus avant le P&L.",
    """📝 *TON VRAI BILAN NE TIENT PAS EN UN CHIFFRE*

As-tu respecté ton risque ?
As-tu évité un trade impulsif ?
As-tu documenté tes erreurs ?

Ce sont ces réponses qui préparent la prochaine progression.

Si tu veux construire ce processus avec l’équipe, découvre l’espace Bectanse.

_Aucun résultat n’est garanti._""",
    button_text="REJOINDRE L’ÉQUIPE")


# SAMEDI — recul et apprentissage, sans pression commerciale excessive
add("SAMEDI", "15/08/2026", "10:00", "Sondage respect du plan",
    "Maintenir l’engagement sans simuler une journée de marché.",
    "Mesurer la discipline vécue pendant la semaine.",
    post_type="poll",
    question="Sur combien de jours as-tu réellement respecté ton plan cette semaine ?",
    options=["0 à 1 jour", "2 à 3 jours", "4 jours", "Les 5 jours"],
    multiple=False)
add("SAMEDI", "15/08/2026", "12:00", "La revue en quatre questions",
    "Éduquer et installer une habitude de journalisation.",
    "Transformer l’expérience de la semaine en données utiles.",
    """📓 *TA REVUE EN 4 QUESTIONS*

1. Quelle décision était parfaitement conforme au plan ?
2. Quelle erreur s’est répétée ?
3. À quel moment l’émotion a pris le dessus ?
4. Quelle règle sera non négociable lundi ?

Écris les réponses. Une semaine non revue risque de devenir une semaine répétée.""")
add("SAMEDI", "15/08/2026", "17:00", "Ne compare pas ton chapitre",
    "Travailler la psychologie et l’appartenance.",
    "Sortir de la comparaison sociale et revenir au processus.",
    """🧠 *LES CAPTURES DES AUTRES NE RACONTENT PAS LEUR HISTOIRE COMPLÈTE*

Tu ne vois ni leur risque, ni leurs pertes, ni leurs erreurs.

Compare ton exécution à ton propre plan. C’est la seule comparaison qui peut réellement améliorer ta prochaine semaine.

Aujourd’hui, prends du recul.""")
add("SAMEDI", "15/08/2026", "20:30", "Demain, on prépare",
    "Créer une transition douce vers le dimanche stratégique.",
    "Réduire la pression commerciale avant la relance dominicale.",
    """🌙 *CE SOIR, ON COUPE. DEMAIN, ON PRÉPARE.*

Profite du recul du week-end.

Dimanche, on construira la checklist, les rendez-vous économiques et le plan mental de la nouvelle semaine.

Reviens avec une page blanche et l’envie d’être plus précis.""", silent=True)


# DIMANCHE — tension, préparation, désir, conversion
add("DIMANCHE", "16/08/2026", "09:30", "Bon dimanche — récupération active",
    "Humaniser la marque et amorcer la préparation.",
    "Le repos fait partie du processus, mais la semaine se prépare.",
    """☀️ *BON DIMANCHE L’ÉQUIPE*

Profite de la matinée. Respire. Prends du recul.

Puis rappelle-toi : une nouvelle semaine ne devient pas différente par hasard.

Elle devient différente quand les règles sont plus claires, le risque mieux défini et les erreurs réellement étudiées.

Aujourd’hui, on prépare la suite ensemble.""")
add("DIMANCHE", "16/08/2026", "12:00", "Combien de semaines vas-tu repousser ?",
    "Créer une prise de conscience sans manipulation excessive.",
    "Le problème peut être l’absence de système, pas l’absence d’information.",
    """⏳ *COMBIEN DE SEMAINES VAS-TU ENCORE REPOUSSER ?*

Tu connais probablement déjà les mots : patience, risque, discipline, journal.

La vraie question est plus inconfortable : lesquels appliques-tu quand personne ne te regarde ?

Tu ne manques peut-être pas d’une nouvelle stratégie.
Tu manques peut-être d’un système que tu acceptes enfin de suivre.""")
add("DIMANCHE", "16/08/2026", "16:00", "Checklist et rendez-vous vérifiés",
    "Démontrer l’expertise et préparer les risques de la semaine.",
    "Trois annonces officielles vérifiées, sans prédiction de résultat.",
    """📅 *3 RENDEZ-VOUS À NOTER CETTE SEMAINE*

🇺🇸 Mercredi 14:30 — IPC américain de juillet
🇺🇸 Jeudi 14:30 — Prix à la production de juillet
🇺🇸 Vendredi 14:30 — Ventes au détail de juillet

Heures de Paris, d’après les calendriers officiels BLS et U.S. Census consultés.

Prépare des scénarios, réduis l’exposition si nécessaire et ne prédis jamais un chiffre avant sa publication.

_Le calendrier indique un timing, pas la direction future du marché._""")
add("DIMANCHE", "16/08/2026", "20:30", "Demain, tout recommence",
    "Convertir après une journée de préparation et de valeur.",
    "Projection dans l’accompagnement quotidien de la semaine suivante.",
    """🔥 *DEMAIN, TOUT RECOMMENCE*

À 08:30 : l’agenda économique vérifié.
À 09:00 : la session Londres.
Puis les quiz, les analyses, les scénarios et les alertes de l’équipe.

Tu peux encore regarder la semaine commencer de l’extérieur.

Ou entrer ce soir avec un plan, un espace et une équipe à retrouver chaque jour.

_Le trading comporte un risque de perte. Aucun gain n’est garanti._""",
    button_text="PRÉPARER MA SEMAINE")
add("DIMANCHE", "16/08/2026", "23:30", "Compte à rebours nouvelle semaine",
    "Créer un dernier rendez-vous et une tension temporelle.",
    "30 minutes avant minuit : décision et identité.",
    """⏳ *PLUS QUE 30 MINUTES*

Dans 30 minutes, une nouvelle semaine commence.

La question n’est pas de savoir si le marché offrira du mouvement.

La question est de savoir si tu arriveras avec des règles assez claires pour ne pas agir au hasard.

À minuit, on repart. Rendez-vous demain à 09:00. 🔥""", silent=True)


# Direction artistique et éditoriale V2.
# Les quiz et sondages restent des formats Telegram natifs : Telegram ne permet
# pas de joindre une photo au même envoi. Leur bannière demeure disponible dans
# la bibliothèque pour créer un teaser séparé lorsque c'est utile.
VISUAL_GROUPS = {
    "london": {
        "Ouverture Londres — cap sur le plan", "Londres — mardi pédagogique",
        "Londres — mercredi de patience", "Londres — jeudi expertise",
        "Londres — vendredi de maîtrise",
    },
    "us": {"Pré-session US du lundi", "Pré-session US — mardi"},
    "economic": {
        "Analyse avant l’IPC", "Avant l’annonce, protéger le capital",
        "Compte à rebours IPC", "Préparation PPI et scénarios",
        "Compte à rebours PPI", "Avant les ventes au détail US",
        "Compte à rebours ventes au détail", "Checklist et rendez-vous vérifiés",
    },
    "alert": {
        "Fenêtre des premiers signaux", "Vérification des alertes — lundi",
        "Préparation des scénarios — mardi", "Alertes et taille de position",
        "Alertes après volatilité", "Vérification des opportunités — jeudi",
        "Dernier point alertes",
    },
    "result": {"Le vrai résultat de la semaine", "La revue en quatre questions"},
}

CTA_BY_GROUP = {
    "london": "VOIR LE PLAN LONDRES",
    "us": "PRÉPARER LA SESSION US",
    "economic": "VOIR LE CALENDRIER",
    "alert": "OUVRIR LES ALERTES",
    "result": "FAIRE MON BILAN",
}

CTA_OVERRIDES = {
    "La semaine commence maintenant": "PRÉPARER MON PLAN",
    "Quiz discipline du lundi": "APPROFONDIR AVEC BECTANSE",
    "Le point d’analyse du lundi": "VOIR L’ANALYSE DU JOUR",
    "Session US Bectanse — lundi": "REJOINDRE LE DESK US",
    "Le choix du lundi soir": "COMMENCER AVEC L’ÉQUIPE",
    "Clôture du lundi, préparation du mardi": "PRÉPARER MA JOURNÉE",
    "Quiz ratio risque/rendement": "APPROFONDIR AVEC BECTANSE",
    "Analyse — risque avant direction": "ACCÉDER À L’ANALYSE",
    "Session US Bectanse — mardi": "REJOINDRE LE DESK US",
    "L’information ne suffit pas": "DÉCOUVRIR L’ACCOMPAGNEMENT",
    "Mercredi sous contrôle": "PRÉPARER LA JOURNÉE MACRO",
    "Quiz anti-FOMO": "APPROFONDIR AVEC BECTANSE",
    "Après l’IPC — revenir au plan": "REJOINDRE LE DESK US",
    "Le calme est une compétence": "TRAVAILLER AVEC L’ÉQUIPE",
    "Transition vers le jeudi technique": "PRÉPARER MON PLAN",
    "Quiz lecture de cassure": "APPROFONDIR AVEC BECTANSE",
    "Analyse — confirmation et invalidation": "OUVRIR L’ANALYSE",
    "Session US Bectanse — jeudi": "REJOINDRE LE DESK US",
    "Monter en compétence": "MONTER EN COMPÉTENCE",
    "Dernière session, même exigence": "PRÉPARER MA SESSION",
    "Sondage bilan de la semaine": "PROGRESSER AVEC BECTANSE",
    "Analyse — finir proprement": "VOIR L’ANALYSE DU JOUR",
    "Session US Bectanse — vendredi": "REJOINDRE LE DESK US",
    "Sondage respect du plan": "PROGRESSER AVEC BECTANSE",
    "Ne compare pas ton chapitre": "REVENIR À L’ESSENTIEL",
    "Demain, on prépare": "PRÉPARER LA PROCHAINE SEMAINE",
    "Bon dimanche — récupération active": "PRÉPARER LA SEMAINE AVEC NOUS",
    "Combien de semaines vas-tu repousser ?": "PASSER À L’ACTION",
    "Demain, tout recommence": "REJOINDRE BECTANSE",
    "Compte à rebours nouvelle semaine": "ÊTRE PRÊT POUR DEMAIN",
}

MENTOR_REWRITES = {
    "La semaine commence maintenant": """🌙 *LA SEMAINE COMMENCE MAINTENANT*

Avant de dormir, prends dix minutes pour toi. Pas pour chercher un trade : pour écrire ton risque maximal, tes priorités et la règle que tu refuses de négocier cette semaine.

Demain à 09:00, je veux te retrouver devant les graphiques avec un plan — pas avec l’envie de te rattraper.

Repose-toi bien l’équipe. On attaque proprement. 🔥""",
    "Ouverture Londres — cap sur le plan": """🇬🇧 *LONDRES EST OUVERTE*

Ce matin, je veux que tu fasses simple : repère tes niveaux, écris ton invalidation et attends que le prix vienne à toi.

Un trader solide n’a pas besoin d’être le premier dans le mouvement. Il a besoin de savoir pourquoi il y entre.

On observe. On confirme. Puis seulement, on agit. 🔥""",
    "Pré-session US du lundi": """🇺🇸 *SESSION US DANS 30 MINUTES*

Avant que le rythme accélère, pose deux scénarios sur papier : ce qui valide ton idée et ce qui l’annule.

Si rien n’est propre, tu ne dois rien au marché. Savoir rester en dehors fait partie du métier.

À 15:00, on se retrouve concentrés et prêts. 🎯""",
    "Le choix du lundi soir": """🌙 *JE VAIS ÊTRE DIRECT AVEC TOI*

Ta semaine ne se transforme pas vendredi soir. Elle se transforme dans les décisions que tu répètes dès maintenant.

Si tu veux arrêter d’avancer seul, retrouve nos analyses, nos alertes et le cadre de travail de l’équipe Bectanse.

Demain, on continue. Ton premier pas peut commencer ce soir.""",
    "Clôture du lundi, préparation du mardi": """🌙 *AVANT DE FERMER LES ÉCRANS*

Note une décision dont tu es fier et une erreur que tu ne veux pas revoir demain. C’est ce travail discret qui fait progresser un trader.

Mardi, on attaque la gestion du risque avec du concret.

Bonne nuit l’équipe. Rendez-vous à 09:00.""",
    "Londres — mardi pédagogique": """🇬🇧 *SESSION LONDRES*

Ce matin, pose-toi cette question avant chaque entrée : combien suis-je réellement prêt à perdre sans changer de comportement ?

Si tu n’as pas la réponse, ta taille de position n’est pas encore maîtrisée.

On observe. On calcule. Puis seulement, on décide.""",
    "Pré-session US — mardi": """🇺🇸 *SESSION US DANS 30 MINUTES*

Le marché va peut-être accélérer. Toi, tu n’as pas besoin d’accélérer avec lui.

Garde ton risque fixe, attends ton contexte et refuse toute entrée que tu ne saurais pas expliquer en une phrase.

On se retrouve à 15:00, lucides et préparés.""",
    "L’information ne suffit pas": """🧠 *JE VAIS TE DIRE CE QUI BLOQUE BEAUCOUP DE TRADERS*

Ce n’est pas le manque d’informations. C’est l’absence d’un processus assez clair pour être appliqué quand l’émotion monte.

Préparer, filtrer, exécuter, revoir : c’est ce cadre que nous travaillons chaque jour chez Bectanse.

Si tu veux avancer avec une méthode et une équipe, l’espace est ouvert.""",
    "Le calme est une compétence": """🌙 *LE CALME SE TRAVAILLE*

Je ne te demande pas de ne rien ressentir. Je te demande de préparer des règles assez claires pour ne pas laisser l’émotion décider à ta place.

Quand le marché accélère, ton plan doit parler plus fort que ton envie d’agir.

Si tu veux construire ce réflexe avec nous, rejoins l’équipe.""",
    "Dernière session, même exigence": """🌙 *ÉCOUTE BIEN AVANT VENDREDI*

Tu n’as rien à prouver au marché. Ne force pas un dernier trade pour sauver ou embellir ta semaine.

Demain, ton seul objectif est de respecter ton plan une fois de plus. Si aucun scénario n’est propre, ne pas trader sera une exécution parfaite.

Rendez-vous à 09:00.""",
    "Le vrai résultat de la semaine": """📝 *JE NE VEUX PAS SEULEMENT VOIR TON P&L*

Dis-moi plutôt : as-tu respecté ton risque ? Évité un trade impulsif ? Documenté une erreur au lieu de la cacher ?

C’est là que se construit la progression durable : dans la qualité des décisions répétées.

Si tu veux installer ce processus avec l’équipe, viens faire ton bilan avec nous.""",
    "Ne compare pas ton chapitre": """🧠 *JE VEUX TE RAPPELER UNE CHOSE IMPORTANTE*

Les captures des autres ne montrent ni leur risque, ni leurs pertes, ni les erreurs commises avant le résultat.

Compare ton exécution à ton propre plan. C’est la seule comparaison capable d’améliorer ta prochaine semaine.

Aujourd’hui, prends du recul et reviens à l’essentiel.""",
    "Bon dimanche — récupération active": """☀️ *BON DIMANCHE L’ÉQUIPE*

Profite de ta matinée. Coupe un peu, respire et prends du recul : le repos fait aussi partie du métier.

Puis reviens avec une question simple : quelle règle rendra ma prochaine semaine plus propre que la précédente ?

Cet après-midi, on prépare la suite ensemble.""",
    "Combien de semaines vas-tu repousser ?": """⏳ *JE VAIS ÊTRE FRANC AVEC TOI*

Tu connais déjà les mots : patience, risque, discipline, journal. Mais lesquels appliques-tu vraiment quand personne ne te regarde ?

Tu n’as peut-être pas besoin d’une stratégie de plus. Tu as peut-être besoin d’un cadre, d’un suivi et d’une équipe qui t’aide à rester constant.

Si tu es prêt à arrêter de repousser, commence aujourd’hui.""",
    "Demain, tout recommence": """🔥 *DEMAIN, JE VEUX TE RETROUVER PRÊT*

À 08:30, l’agenda économique vérifié. À 09:00, la session Londres. Puis les quiz, les analyses, les scénarios et les alertes de l’équipe.

Tu peux encore regarder la semaine commencer de l’extérieur.

Ou entrer ce soir avec un plan clair et une équipe à retrouver chaque jour.""",
}


def apply_editorial_direction():
    """Applique les visuels, les CTA et la voix mentor à toute la semaine."""
    visual_by_name = {
        name: group for group, names in VISUAL_GROUPS.items() for name in names
    }
    for post in posts:
        group = visual_by_name.get(post["name"])
        if post["post_type"] == "message" and group:
            post["image_url"] = VISUALS[group]
        if post["name"] in MENTOR_REWRITES:
            post["message"] = MENTOR_REWRITES[post["name"]].strip()
        # Les mentions réglementaires figurent déjà sur le site et ne sont pas
        # répétées mécaniquement sous chaque message Telegram.
        paragraphs = [
            paragraph for paragraph in post["message"].split("\n\n")
            if not (
                paragraph.startswith("_Le trading comporte")
                or paragraph.startswith("_Aucun résultat")
                or paragraph.startswith("_Aucun gain")
                or paragraph.startswith("_Contenu éducatif")
            )
        ]
        post["message"] = "\n\n".join(paragraphs).strip()
        post["button_text"] = (
            CTA_OVERRIDES.get(post["name"])
            or (CTA_BY_GROUP.get(group) if group else "DÉCOUVRIR BECTANSE")
        )


apply_editorial_direction()


def csv_row(post):
    post_type = "sondage" if post["post_type"] == "poll" else post["post_type"]
    return {
        "nom": post["name"],
        "type": post_type,
        "date": post["date"],
        "heure": post["time"],
        "rythme": "unique",
        "jours": "",
        "semaine_rotation": "",
        "canal": CHANNEL,
        "message": post["message"] if post["post_type"] == "message" else "",
        "image_url": post["image_url"],
        "texte_bouton": post["button_text"],
        "lien_bouton": SITE if post["button_text"] else "",
        "question": post["question"],
        "reponses": "|".join(post["options"]),
        "bonnes_reponses": "|".join(str(index) for index in post["correct"]),
        "explication": post["explanation"],
        "anonyme": "oui",
        "choix_multiples": "oui" if post["multiple"] else "non",
        "silencieux": "oui" if post["silent"] else "non",
        "actif": "oui",
        "tous_les_canaux": "oui",
        "canaux": "",
    }


def validate():
    csv_posts = [post for post in posts if post["include_csv"]]
    assert len(csv_posts) == 54, f"54 publications CSV attendues, {len(csv_posts)} obtenues"
    assert len(posts) == 59, f"59 créneaux attendus avec agendas, {len(posts)} obtenus"
    assert len({(post["date"], post["time"]) for post in posts}) == len(posts)
    for post in csv_posts:
        if post["post_type"] == "message":
            caption_limit = 1024 if post["image_url"] else 4096
            assert post["message"] and len(post["message"]) <= caption_limit
        else:
            assert not post["image_url"], "Un quiz natif ne peut pas recevoir d’image"
            assert 2 <= len(post["options"]) <= 12
            assert len(post["question"]) <= 300
            assert all(1 <= index <= len(post["options"]) for index in post["correct"])
            if post["post_type"] == "quiz":
                assert post["correct"]
            assert len(post["explanation"]) <= 200
        assert post["button_text"] and len(post["button_text"]) <= 64
    return csv_posts


def build_markdown():
    lines = [
        "# Planning Telegram Bectanse Académie — 10 au 16 août 2026",
        "",
        "## Stratégie globale",
        "",
        "La semaine suit la progression : attention → engagement → confiance → clic → conversion. "
        "Chaque publication possède un CTA contextuel vers Bectanse, sans inventer une opportunité. "
        "Les messages liés aux analyses, signaux et alertes invitent à vérifier uniquement les mises "
        "à jour réellement disponibles.",
        "",
        "L’agenda de 08:30 est volontairement généré en direct par le robot économique et n’est pas "
        "dupliqué dans le CSV. Si sa source est indisponible, aucun message public n’est envoyé.",
        "",
    ]
    current_day = None
    for post in posts:
        if post["day"] != current_day:
            current_day = post["day"]
            lines.extend([f"## 📅 {current_day}", ""])
        lines.extend([
            f"### {post['time']} — {post['name']}",
            "",
            f"**Objectif :** {post['objective']}",
            "",
            f"**Angle :** {post['angle']}",
            "",
            "**Message :**",
            "",
            post["message"] if post["message"] else post["question"],
        ])
        if post["post_type"] != "message":
            lines.extend(["", "**Réponses :**"])
            for index, option in enumerate(post["options"], start=1):
                marker = " — bonne réponse" if index in post["correct"] else ""
                lines.append(f"- {index}. {option}{marker}")
            if post["explanation"]:
                lines.extend(["", f"**Explication :** {post['explanation']}"])
        if post["button_text"]:
            lines.extend(["", f"**CTA :** {post['button_text']} → {SITE}"])
        elif not post["include_csv"]:
            lines.extend(["", "**CTA :** aucun — publication informative automatique."])
        else:
            lines.extend(["", "**CTA :** aucun."])
        lines.extend(["", "---", ""])
    lines.extend([
        "## Sources économiques vérifiées",
        "",
        "- U.S. Bureau of Labor Statistics — calendrier 2026 : https://www.bls.gov/schedule/2026/",
        "- U.S. Bureau of Labor Statistics — CPI : https://www.bls.gov/cpi/",
        "- U.S. Bureau of Labor Statistics — PPI : https://www.bls.gov/schedule/news_release/ppi.htm",
        "- U.S. Census Bureau — ventes au détail : https://www.census.gov/retail/release_schedule.html",
        "",
    ])
    return "\n".join(lines)


def main():
    csv_posts = validate()
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file, fieldnames=CSV_COLUMNS, delimiter=";", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(csv_row(post) for post in csv_posts)
    PLAN_PATH.write_text(build_markdown(), encoding="utf-8")
    print(f"{len(csv_posts)} publications CSV créées : {CSV_PATH}")
    print(f"{len(posts)} créneaux documentés : {PLAN_PATH}")


if __name__ == "__main__":
    main()
