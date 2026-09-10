# Bectanse Track — App Store release

## Application

- Bundle ID: `com.bectanse.track`
- Apple team: `72C5MFHM2S`
- Version: `1.0`
- Build: `5`
- Category: Finance
- Minimum iOS: 17.0

## Publisher

- Legal entity: `BECTANSE L.L.C.`
- Jurisdiction: New Mexico, United States
- New Mexico Secretary of State file number: `3291799`
- Formation date: August 27, 2026
- Principal address: `1209 Mountain Road PL NE, STE R, Albuquerque, NM 87110, United States`

Before submitting under the company name, the Apple Developer membership and
App Store Connect seller must represent `BECTANSE L.L.C.`. Do not submit this
release under the current individual seller identity if the intended public
seller is the LLC.

## Version 1 access model

The first public version is a free companion app reserved for active Bectanse
Academy members. It contains no purchase, free trial, subscription paywall or
link encouraging an external purchase.

- Authentication uses the member's existing BCT code.
- The backend verifies the Academy membership before creating the mobile
  session and before every trading API request.
- Expired, cancelled, suspended, Explorer and demo accounts receive no journal
  or MT5 access.
- Access is withdrawn automatically when the Academy subscription expires.

Keep the future StoreKit infrastructure dormant. Do not attach the PRO or ELITE
in-app purchases to this App Store version. A standalone paid version can be
enabled later through a new reviewed build.

## Server configuration

Leave `BECTANSE_TRACK_STANDALONE_ENABLED` unset or set it to `false` in
production. StoreKit context, purchase synchronization and standalone account
creation are then unavailable while the website Journal architecture remains
unchanged.

Before a future standalone release, configure `APPLE_APP_ID`, the subscription
products and App Store Server Notifications, then explicitly set
`BECTANSE_TRACK_STANDALONE_ENABLED=true` in the reviewed release environment.

## App Review access

Provide Apple with a dedicated active Academy review account and code BCT.
Keep its MT5 demo account connected and the backend available for the full
review period. Explain in Review Notes that Bectanse Track is the free mobile
companion to the existing Bectanse Academy service and contains no commerce or
external purchase call to action.

## Public metadata

- App name: `Bectanse Track`
- Subtitle: `Votre trading sous contrôle`
- Promotional text: `Vos performances MetaTrader, votre discipline et vos axes de progression dans un journal privé réservé aux membres Bectanse.`
- Keywords: `trading,journal,MetaTrader,MT5,performance,statistiques,risque,discipline,coach,bourse`
- Category: `Finance`
- Copyright: `2026 BECTANSE L.L.C.`
- Release: automatic after App Review approval
- Privacy: `https://acces.bectanse-academie.com/bectanse-track/legal/confidentialite`
- Terms: `https://acces.bectanse-academie.com/bectanse-track/legal/conditions`
- Support: `https://acces.bectanse-academie.com/bectanse-track/legal/support`
- Support email: `support@bectanse-academie.com`

### Description (French)

Bectanse Track transforme votre historique MetaTrader 5 en un journal de
performance clair, précis et directement exploitable.

Consultez votre P&L, votre balance, votre equity, votre taux de réussite et
votre courbe de performance. Retrouvez chaque position clôturée avec ses prix
d’entrée et de sortie, son volume, sa durée et ses frais. Le calendrier restitue
vos résultats jour après jour, tandis que les Analytics mettent en évidence vos
performances par actif, session et période.

Bectanse Coach analyse vos données vérifiées avec des règles déterministes pour
faire ressortir vos habitudes de discipline, de risque, de régularité,
d’exécution et de timing. Il ne génère aucun signal et ne passe aucun ordre.

Cette première version est le compagnon mobile de Bectanse Académie. Elle est
réservée aux membres disposant d’un abonnement actif et s’utilise avec le code
BCT personnel déjà fourni par l’Académie.

### App Review notes

Bectanse Track is the free mobile companion app for existing active Bectanse
Academy members. The app contains no purchase flow, trial, subscription paywall
or external purchase link. Reviewers can sign in with the dedicated BCT review
code supplied in App Review Information. A connected MetaTrader demo account
must remain available throughout review. The app is read-only: it imports
trading history and never places orders or generates trading signals.

### App Privacy declarations

- Contact Info: name and email address; linked to the user; app functionality.
- Identifiers: user ID/member code; linked to the user; app functionality.
- Purchases: Academy membership status; linked to the user; app functionality.
- Financial Info: MetaTrader account and trading history; linked to the user;
  app functionality and product personalization.
- Tracking: no.

### Screenshots

The five French iPhone 6.9-inch JPEG screenshots are stored in
`AppStore/Screenshots/fr-FR`. Each image is 1320 × 2868 pixels, without an alpha
channel, and covers Overview, Trades, Calendar, Analytics and Coach.

Build 5 contains no StoreKit paywall, purchase action, trial claim or external
purchase link. The public legal pages identify `BECTANSE L.L.C.` as the editor
and data controller for Bectanse Track.
