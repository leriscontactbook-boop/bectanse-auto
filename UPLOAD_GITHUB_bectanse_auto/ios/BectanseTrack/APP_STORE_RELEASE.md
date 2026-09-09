# Bectanse Track — App Store release

## Application

- Bundle ID: `com.bectanse.track`
- Apple team: `72C5MFHM2S`
- Version: `1.0`
- Build: `3`
- Category: Finance
- Minimum iOS: 17.0

## Auto-renewable subscriptions

Create both products in the same subscription group so Apple manages upgrades
and prevents simultaneous subscriptions:

- `com.bectanse.track.pro.monthly` → `JOURNAL_PRO`
- `com.bectanse.track.elite.monthly` → `JOURNAL_ELITE`

Configure a seven-day free introductory offer for the subscription group in
App Store Connect. StoreKit checks the Apple Account's introductory-offer
eligibility before the paywall advertises the trial. The app creates only a
locked profile before purchase; Journal access starts solely after the server
has verified Apple's signed transaction. Prices and localized product
descriptions remain App Store catalog data and are never hardcoded in the app.

Changing iPhone or the e-mail entered in Bectanse Track does not reset the
introductory offer for the same Apple Account. The backend also uniquely binds
the original Apple transaction and every received transaction to one Bectanse
profile, preventing purchase reuse between profiles.

## Server configuration

Set the numeric App Store application ID as `APPLE_APP_ID` in production. If
the product IDs are changed in App Store Connect, also set
`APPLE_TRACK_PRO_PRODUCT_ID` and `APPLE_TRACK_ELITE_PRODUCT_ID`.

Use this App Store Server Notifications V2 URL for Production and Sandbox:

`https://acces.bectanse-academie.com/api/mobile/storekit/notifications`

The backend verifies Apple JWS signatures against Apple Root CA G3, the bundle
ID, environment, product ID, expiry, revocation state, account token and
transaction ownership before changing access.

## Public metadata

- Privacy: `https://acces.bectanse-academie.com/bectanse-track/legal/confidentialite`
- Terms: `https://acces.bectanse-academie.com/bectanse-track/legal/conditions`
- Support: `support@bectanse-academie.com`

The StoreKit paywall includes the live Apple price, automatic-renewal notice,
restore action, terms and privacy links.
