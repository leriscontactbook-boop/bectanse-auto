import hashlib
import json
import os
from datetime import date

import requests
from flask import Response, jsonify, render_template, request


SITE_URL = os.environ.get(
    "PUBLIC_SITE_URL", "https://acces.bectanse-academie.com"
).rstrip("/")
SITE_NAME = "Bectanse Académie"
SEO_LAST_UPDATED = os.environ.get("SEO_LAST_UPDATED", "2026-08-29")
INDEXNOW_KEY = os.environ.get(
    "INDEXNOW_KEY",
    hashlib.sha256((SITE_URL + ":bectanse-indexnow").encode("utf-8")).hexdigest()[:32],
)


SEO_GUIDES = {
    "trading-or-xauusd": {
        "title": "Trading de l’or (XAU/USD) : comprendre le marché avant d’agir",
        "seo_title": "Trading de l’or XAU/USD : guide complet | Bectanse Académie",
        "description": "Comprendre le XAU/USD, ses horaires, ses principaux moteurs et les règles de préparation essentielles avant toute décision de trading.",
        "eyebrow": "Guide XAU/USD",
        "reading_minutes": 8,
        "intro": "L’or est un marché rapide, liquide et sensible au dollar, aux taux d’intérêt et aux périodes d’incertitude. Une méthode sérieuse commence par le contexte, pas par une entrée prise au hasard sur un graphique.",
        "sections": [
            {
                "title": "Ce que représente réellement le XAU/USD",
                "paragraphs": [
                    "XAU désigne l’or et USD le dollar américain. La cotation indique donc la valeur d’une once d’or exprimée en dollars. Quand le prix monte, l’or se renforce face au dollar. Quand il baisse, le dollar se renforce ou la demande d’or diminue.",
                    "Cette relation n’est jamais mécanique. Les anticipations de taux, les rendements obligataires, les annonces économiques et la recherche de sécurité peuvent agir en même temps. C’est pourquoi une lecture professionnelle distingue toujours le contexte, la structure du prix et le déclencheur d’entrée.",
                ],
            },
            {
                "title": "Les moments qui demandent le plus d’attention",
                "paragraphs": [
                    "Les volumes évoluent avec les sessions de Londres et de New York. Les statistiques américaines importantes peuvent provoquer des accélérations, des écarts et des mouvements très rapides. Le calendrier économique doit être consulté avant toute prise de position.",
                    "Une forte volatilité n’est pas automatiquement une opportunité. Si le risque ne peut pas être défini clairement, ne pas intervenir reste une décision de trading à part entière.",
                ],
            },
            {
                "title": "Une lecture en trois niveaux",
                "paragraphs": [
                    "Commence par l’unité de temps supérieure pour déterminer la tendance ou la phase de range. Identifie ensuite les zones où le marché a réellement réagi. Termine sur l’unité d’exécution en attendant un signal compatible avec ton scénario.",
                    "Le plan doit préciser l’invalidation avant l’objectif. Une analyse utile ne dit pas seulement où le prix pourrait aller : elle explique à quel moment l’idée n’est plus valide.",
                ],
            },
        ],
        "checklist": [
            "Consulter les annonces économiques avant la session",
            "Définir la tendance ou le range sur une unité supérieure",
            "Tracer peu de zones, mais des zones justifiées",
            "Fixer l’invalidation et le risque avant l’entrée",
            "Documenter la décision dans un journal de trading",
        ],
        "faq": [
            ("Le XAU/USD convient-il à un débutant ?", "Il peut être étudié par un débutant, mais sa volatilité exige une taille de position prudente, un plan écrit et une phase d’apprentissage sans précipitation."),
            ("Quelle est la meilleure heure pour trader l’or ?", "Il n’existe pas d’heure universelle. Les sessions de Londres et de New York sont souvent plus actives, mais l’agenda économique et la stratégie utilisée restent déterminants."),
        ],
    },
    "gestion-risque-trading": {
        "title": "Gestion du risque en trading : protéger son capital avant de chercher la performance",
        "seo_title": "Gestion du risque en trading : méthode claire | Bectanse Académie",
        "description": "Construire un plan de risque cohérent : invalidation, taille de position, ratio risque-rendement et limites quotidiennes.",
        "eyebrow": "Méthode et discipline",
        "reading_minutes": 7,
        "intro": "La première responsabilité d’un trader n’est pas de prédire chaque mouvement. Elle consiste à décider à l’avance ce qu’il accepte de perdre si son scénario est invalidé.",
        "sections": [
            {
                "title": "Le risque se décide avant l’entrée",
                "paragraphs": [
                    "Une position ne devrait jamais être dimensionnée uniquement selon la conviction. Le niveau d’invalidation, la distance du stop et la part de capital risquée déterminent ensemble la taille cohérente.",
                    "Déplacer l’invalidation pour éviter une perte transforme un risque prévu en risque incontrôlé. Le plan doit être suffisamment simple pour rester applicable lorsque la pression augmente.",
                ],
            },
            {
                "title": "Penser en série plutôt qu’en trade isolé",
                "paragraphs": [
                    "Une bonne décision peut produire une perte et une mauvaise décision peut parfois produire un gain. La qualité se mesure donc sur une série : respect du plan, risque moyen, erreurs répétées et régularité d’exécution.",
                    "Le ratio risque-rendement aide à comparer un objectif au risque accepté, mais il ne remplace pas la probabilité du scénario. Un ratio affiché sans justification technique ne suffit pas.",
                ],
            },
            {
                "title": "Les limites qui protègent la journée",
                "paragraphs": [
                    "Une limite de perte quotidienne et un nombre maximal de décisions évitent la spirale du rattrapage. Après une erreur émotionnelle, la priorité n’est pas de récupérer immédiatement : elle est de retrouver un état de décision normal.",
                ],
            },
        ],
        "checklist": [
            "Connaître la perte maximale avant de cliquer",
            "Calculer la taille selon la distance d’invalidation",
            "Fixer une limite de perte quotidienne",
            "Refuser une position dont le risque n’est pas lisible",
            "Évaluer le processus sur une série de trades",
        ],
        "faq": [
            ("Quel pourcentage faut-il risquer ?", "Il n’existe pas de chiffre adapté à tous. Le niveau doit rester compatible avec le capital, l’expérience, la volatilité et la perte maximale supportable."),
            ("Un bon ratio garantit-il un bon trade ?", "Non. Le ratio décrit une relation entre risque et objectif. Il ne prouve ni la probabilité du scénario ni la qualité de l’exécution."),
        ],
    },
    "journal-trading": {
        "title": "Journal de trading : transformer chaque décision en progression mesurable",
        "seo_title": "Journal de trading : quoi noter et analyser | Bectanse Académie",
        "description": "Les informations essentielles d’un journal de trading utile pour mesurer la discipline, repérer les erreurs et progresser sur des données réelles.",
        "eyebrow": "Progression mesurable",
        "reading_minutes": 6,
        "intro": "Sans historique fiable, la mémoire sélectionne les gains, minimise les erreurs et rend les progrès difficiles à mesurer. Le journal transforme les impressions en faits.",
        "sections": [
            {
                "title": "Noter le contexte, pas seulement le résultat",
                "paragraphs": [
                    "Le marché, l’unité de temps, la session, le scénario, l’entrée, l’invalidation, les objectifs et le risque doivent être enregistrés avant ou au moment de la décision. Une capture du graphique permet ensuite de vérifier si la lecture était cohérente.",
                    "Le gain ou la perte ne suffit pas. Il faut aussi noter si le plan a été respecté, si l’entrée a été anticipée et dans quel état émotionnel la décision a été prise.",
                ],
            },
            {
                "title": "Faire une revue hebdomadaire utile",
                "paragraphs": [
                    "La revue cherche des répétitions : mêmes horaires, mêmes erreurs, mêmes configurations efficaces. Elle ne sert pas à juger chaque résultat isolément, mais à améliorer une règle précise pour la semaine suivante.",
                    "Une seule action d’amélioration bien suivie vaut mieux qu’une longue liste oubliée dès la prochaine session.",
                ],
            },
        ],
        "checklist": [
            "Capture avant et après la décision",
            "Scénario, invalidation et objectifs écrits",
            "Risque prévu et résultat exprimés dans la même unité",
            "État émotionnel et respect du plan",
            "Une priorité d’amélioration par semaine",
        ],
        "faq": [
            ("Faut-il noter tous les trades ?", "Oui, si l’objectif est d’obtenir des statistiques représentatives. Ne conserver que les meilleures décisions crée une lecture faussée."),
            ("Quand effectuer la revue ?", "Une courte note après la décision puis une revue hebdomadaire permettent de séparer l’observation immédiate de l’analyse à froid."),
        ],
    },
    "psychologie-trader": {
        "title": "Psychologie du trader : construire une discipline qui résiste à la pression",
        "seo_title": "Psychologie du trader et discipline | Bectanse Académie",
        "description": "Comprendre les automatismes émotionnels du trading et mettre en place des règles concrètes pour décider avec davantage de discipline.",
        "eyebrow": "Mental et exécution",
        "reading_minutes": 7,
        "intro": "La confiance utile ne vient pas d’une certitude sur le prochain mouvement. Elle vient d’un processus répété, mesuré et suffisamment clair pour être suivi même après une perte.",
        "sections": [
            {
                "title": "Reconnaître les décisions émotionnelles",
                "paragraphs": [
                    "La peur de manquer une entrée, l’envie de récupérer une perte et l’excès de confiance après un gain poussent à modifier le plan. Ces réactions sont normales, mais elles deviennent coûteuses lorsqu’aucune règle ne les interrompt.",
                    "Nommer l’émotion avant la décision crée une pause. Si l’urgence est plus forte que la clarté du scénario, il est souvent préférable de ne pas intervenir.",
                ],
            },
            {
                "title": "Construire la confiance sur des preuves",
                "paragraphs": [
                    "Une routine, un journal et des limites vérifiables donnent une base concrète. La confiance grandit lorsque le trader observe qu’il respecte ses règles sur une série, pas lorsqu’il cherche une promesse de gain.",
                    "Le bon objectif quotidien peut être comportemental : attendre la confirmation, respecter le risque ou ne prendre que les configurations prévues.",
                ],
            },
        ],
        "checklist": [
            "Décrire son état avant la session",
            "Écrire les conditions qui autorisent une entrée",
            "Prévoir une pause après une erreur impulsive",
            "Mesurer le respect du plan indépendamment du résultat",
            "Réduire la fréquence lorsque la qualité baisse",
        ],
        "faq": [
            ("Comment éviter le revenge trading ?", "Une limite quotidienne, une pause obligatoire et l’impossibilité de reprendre sans nouvelle analyse réduisent la décision de rattrapage."),
            ("La confiance vient-elle des gains ?", "Les gains peuvent rassurer temporairement. Une confiance durable repose davantage sur un processus compris et répété."),
        ],
    },
    "analyse-trading-ia": {
        "title": "Analyse de trading avec l’IA : ce qu’un assistant peut faire, et ce qu’il ne doit pas décider",
        "seo_title": "Analyse trading IA : méthode et limites | Bectanse Académie",
        "description": "Utiliser une IA pour structurer la lecture d’un graphique sans confondre assistance, certitude et conseil financier personnalisé.",
        "eyebrow": "Outils du trader",
        "reading_minutes": 6,
        "intro": "Une intelligence artificielle peut accélérer la mise en forme d’un scénario, relever des zones visibles et rappeler une méthode. Elle ne transforme jamais une capture en certitude de marché.",
        "sections": [
            {
                "title": "Une aide à la lecture et à la préparation",
                "paragraphs": [
                    "Une analyse assistée peut organiser la tendance, les zones, le scénario principal, l’invalidation et plusieurs objectifs. L’utilisateur doit fournir un graphique lisible, le marché, l’unité de temps et le contexte utile.",
                    "Le résultat est plus pertinent lorsqu’il reste court, justifié et compatible avec les niveaux réellement visibles sur l’image.",
                ],
            },
            {
                "title": "Les limites à conserver",
                "paragraphs": [
                    "Une capture ne contient pas toujours le calendrier économique, la liquidité réelle ou les changements intervenus après sa création. Chaque niveau doit donc être vérifié sur la plateforme avant toute décision.",
                    "L’outil sert à structurer la réflexion. La validation du risque et la décision finale restent toujours sous le contrôle de l’utilisateur.",
                ],
            },
        ],
        "checklist": [
            "Utiliser une capture nette avec l’échelle des prix visible",
            "Préciser le marché et l’unité de temps",
            "Vérifier chaque niveau sur la plateforme",
            "Consulter le calendrier économique séparément",
            "Ne jamais confondre scénario et garantie",
        ],
        "faq": [
            ("Une IA peut-elle garantir un objectif ?", "Non. Elle peut proposer un scénario à partir des informations disponibles, mais aucun objectif de marché n’est garanti."),
            ("Pourquoi vérifier les niveaux ?", "Parce qu’une image peut être recadrée, ancienne ou incomplète. La plateforme de trading reste la référence pour le prix actuel."),
        ],
    },
}


def _guide_url(slug):
    return f"{SITE_URL}/guides/{slug}"


def indexable_pages():
    pages = [
        {
            "path": "/vip",
            "title": "Bectanse Académie — Formation, application et accompagnement trading",
            "description": "Découvre l’écosystème Bectanse Académie, ses outils, sa formation et son accompagnement avant de créer gratuitement ton accès Explorer.",
            "priority": "1.0",
            "changefreq": "weekly",
        },
        {
            "path": "/guides",
            "title": "Guides trading gratuits — Bectanse Académie",
            "description": "Des guides clairs sur le XAU/USD, le risque, le journal de trading, la psychologie et les outils d’analyse.",
            "priority": "0.9",
            "changefreq": "weekly",
        },
    ]
    for slug, guide in SEO_GUIDES.items():
        pages.append(
            {
                "path": f"/guides/{slug}",
                "title": guide["seo_title"],
                "description": guide["description"],
                "priority": "0.8",
                "changefreq": "monthly",
            }
        )
    return pages


def submit_indexnow_urls():
    urls = [SITE_URL + page["path"] for page in indexable_pages()]
    payload = {
        "host": SITE_URL.replace("https://", "").replace("http://", ""),
        "key": INDEXNOW_KEY,
        "keyLocation": f"{SITE_URL}/{INDEXNOW_KEY}.txt",
        "urlList": urls,
    }
    try:
        response = requests.post(
            "https://api.indexnow.org/indexnow",
            json=payload,
            timeout=12,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        return {
            "ok": response.status_code in {200, 202},
            "status": response.status_code,
            "urls": len(urls),
        }
    except Exception as error:
        return {"ok": False, "status": 0, "urls": len(urls), "error": str(error)[:240]}


def _structured_organization():
    return {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "EducationalOrganization",
                "@id": f"{SITE_URL}/#organization",
                "name": SITE_NAME,
                "url": f"{SITE_URL}/vip",
                "logo": f"{SITE_URL}/static/icons/bectanse-app-icon-master.png",
                "founder": {"@type": "Person", "name": "Leris Luketo"},
                "legalName": "LERIS CORP FZCO",
                "description": "Académie en ligne consacrée à la formation, à la discipline et aux outils d’accompagnement des traders.",
            },
            {
                "@type": "WebSite",
                "@id": f"{SITE_URL}/#website",
                "url": f"{SITE_URL}/vip",
                "name": SITE_NAME,
                "alternateName": "Bectanse",
                "publisher": {"@id": f"{SITE_URL}/#organization"},
                "inLanguage": "fr-FR",
            },
        ],
    }


def _guide_structured_data(slug, guide):
    return {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Article",
                "headline": guide["title"],
                "description": guide["description"],
                "datePublished": SEO_LAST_UPDATED,
                "dateModified": SEO_LAST_UPDATED,
                "inLanguage": "fr-FR",
                "mainEntityOfPage": _guide_url(slug),
                "author": {"@type": "Organization", "name": SITE_NAME},
                "publisher": {
                    "@type": "Organization",
                    "name": SITE_NAME,
                    "logo": {
                        "@type": "ImageObject",
                        "url": f"{SITE_URL}/static/icons/bectanse-app-icon-master.png",
                    },
                },
                "image": f"{SITE_URL}/static/vip/assets/bectanse-brain-choice-v1.png",
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Bectanse Académie", "item": f"{SITE_URL}/vip"},
                    {"@type": "ListItem", "position": 2, "name": "Guides", "item": f"{SITE_URL}/guides"},
                    {"@type": "ListItem", "position": 3, "name": guide["title"], "item": _guide_url(slug)},
                ],
            },
        ],
    }


def _organic_summary(get_conn):
    empty = {"visitors": 0, "sessions": 0, "page_views": 0}
    try:
        conn = get_conn()
        rows = conn.run(
            """SELECT COUNT(DISTINCT visitor_id),COUNT(DISTINCT session_id),
                      COUNT(*) FILTER (WHERE event_name='page_view')
               FROM analytics_events
               WHERE created_at >= NOW() - INTERVAL '30 days'
                 AND (LOWER(source) LIKE '%google%' OR LOWER(source) LIKE '%bing%'
                      OR LOWER(referrer_host) LIKE '%google.%'
                      OR LOWER(referrer_host) LIKE '%bing.%')"""
        )
        conn.close()
        if rows:
            return {
                "visitors": int(rows[0][0] or 0),
                "sessions": int(rows[0][1] or 0),
                "page_views": int(rows[0][2] or 0),
            }
    except Exception:
        return empty
    return empty


def register_seo_features(app, admin_required, get_conn):
    public_paths = {page["path"] for page in indexable_pages()}
    google_verification = os.environ.get("GOOGLE_SITE_VERIFICATION", "").strip()
    bing_verification = os.environ.get("BING_SITE_VERIFICATION", "").strip()

    @app.route("/guides")
    def seo_guides_index():
        page = next(item for item in indexable_pages() if item["path"] == "/guides")
        json_ld = {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": page["title"],
            "description": page["description"],
            "url": f"{SITE_URL}/guides",
            "inLanguage": "fr-FR",
            "publisher": {"@type": "Organization", "name": SITE_NAME},
            "hasPart": [
                {"@type": "Article", "name": guide["title"], "url": _guide_url(slug)}
                for slug, guide in SEO_GUIDES.items()
            ],
        }
        return render_template(
            "seo_guides.html",
            guides=SEO_GUIDES,
            seo=page,
            site_url=SITE_URL,
            json_ld=json.dumps(json_ld, ensure_ascii=False),
            google_verification=google_verification,
            bing_verification=bing_verification,
        )

    @app.route("/guides/<slug>")
    def seo_guide_detail(slug):
        guide = SEO_GUIDES.get(slug)
        if not guide:
            return "Guide introuvable", 404
        related = [(key, value) for key, value in SEO_GUIDES.items() if key != slug][:3]
        return render_template(
            "seo_guide_detail.html",
            slug=slug,
            guide=guide,
            related=related,
            site_url=SITE_URL,
            json_ld=json.dumps(_guide_structured_data(slug, guide), ensure_ascii=False),
            google_verification=google_verification,
            bing_verification=bing_verification,
        )

    @app.route("/robots.txt")
    def robots_txt():
        body = "\n".join(
            [
                "User-agent: *",
                "Allow: /vip",
                "Allow: /guides",
                "Disallow: /admin",
                "Disallow: /api/",
                "Disallow: /accueil",
                "Disallow: /dashboard",
                "Disallow: /academie",
                "Disallow: /canal",
                "Disallow: /profil",
                "Disallow: /explorer",
                "Disallow: /analyse-ia",
                "Disallow: /abonnement",
                "Disallow: /inscription",
                "Disallow: /rejoindre/",
                "Disallow: /legal/",
                "",
                f"Sitemap: {SITE_URL}/sitemap.xml",
                "",
            ]
        )
        return Response(body, mimetype="text/plain")

    @app.route("/sitemap.xml")
    def sitemap_xml():
        urls = []
        for page in indexable_pages():
            urls.append(
                "<url>"
                f"<loc>{SITE_URL}{page['path']}</loc>"
                f"<lastmod>{SEO_LAST_UPDATED}</lastmod>"
                f"<changefreq>{page['changefreq']}</changefreq>"
                f"<priority>{page['priority']}</priority>"
                "</url>"
            )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(urls)
            + "</urlset>"
        )
        return Response(xml, mimetype="application/xml")

    def indexnow_key_file():
        return Response(INDEXNOW_KEY, mimetype="text/plain")

    app.add_url_rule(
        f"/{INDEXNOW_KEY}.txt",
        endpoint="indexnow_key_file",
        view_func=indexnow_key_file,
    )

    @app.route("/admin/seo")
    @admin_required
    def admin_seo():
        return render_template(
            "admin_seo.html",
            pages=indexable_pages(),
            site_url=SITE_URL,
            indexnow_key=INDEXNOW_KEY,
            organic=_organic_summary(get_conn),
            google_connected=bool(google_verification),
            bing_connected=bool(bing_verification),
        )

    @app.route("/admin/api/seo/indexnow", methods=["POST"])
    @admin_required
    def admin_submit_indexnow():
        result = submit_indexnow_urls()
        return jsonify(result), (200 if result.get("ok") else 502)

    @app.after_request
    def apply_seo_policy(response):
        path = (request.path or "/").rstrip("/") or "/"
        if path.startswith("/static/"):
            return response
        if path in public_paths and response.status_code == 200:
            response.headers["X-Robots-Tag"] = "index, follow, max-image-preview:large, max-snippet:-1"
        else:
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"

        content_type = response.headers.get("Content-Type", "")
        if response.status_code == 200 and content_type.startswith("text/html") and path in {"/", "/vip"}:
            try:
                if response.direct_passthrough:
                    response.direct_passthrough = False
                body = response.get_data(as_text=True)
                additions = []
                if google_verification and 'name="google-site-verification"' not in body:
                    additions.append(f'<meta name="google-site-verification" content="{google_verification}">')
                if bing_verification and 'name="msvalidate.01"' not in body:
                    additions.append(f'<meta name="msvalidate.01" content="{bing_verification}">')
                if path == "/vip" and '"@id":"' + SITE_URL + '/#organization"' not in body.replace(" ", ""):
                    additions.append(
                        '<script type="application/ld+json">'
                        + json.dumps(_structured_organization(), ensure_ascii=False)
                        + "</script>"
                    )
                if additions and "</head>" in body:
                    response.set_data(body.replace("</head>", "\n".join(additions) + "\n</head>", 1))
                    response.headers.pop("Content-Length", None)
            except Exception:
                pass
        return response

