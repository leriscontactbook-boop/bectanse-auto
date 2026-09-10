# Bectanse Track — App Store release

## Application

- Bundle ID: `com.bectanse.track`
- Apple team: `72C5MFHM2S`
- Version: `1.0`
- Build: `4`
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

- Privacy: `https://acces.bectanse-academie.com/bectanse-track/legal/confidentialite`
- Terms: `https://acces.bectanse-academie.com/bectanse-track/legal/conditions`
- Support: `support@bectanse-academie.com`

Build 4 contains no StoreKit paywall, purchase action, trial claim or external
purchase link. The public legal pages identify `BECTANSE L.L.C.` as the editor
and data controller for Bectanse Track.
