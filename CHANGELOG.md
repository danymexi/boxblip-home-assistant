# Changelog

## 0.3.0

- Several accounts at one store: each parcel imported from one of them has an `account` attribute (for example
  `Amazon · Casa`), also in the `boxblip_parcel_updated` event; the On the way counter has a `by_account` attribute
  with the parcels on the way per account. Needs an up-to-date BoxBlip app; parcels without an account and
  older servers keep working (the attribute is empty).
- Dashboard: each row names its store account, the summary counts the parcels on the way per account and a new
  *Per account* card lists them under each account (hidden when no parcel has an account).
- Diagnostics also hide account names.
- README: voice announcements on Alexa, Google Nest and HomePod when a parcel is out for delivery; a card and
  templates for the parcels of one store account.

## 0.2.0

First public release.

- One device per parcel, linked to a BoxBlip account device, with status, expected delivery, last update, last
  event, last place, carrier and tracking number.
- Counters (on the way, out for delivery, to pick up, delivered today, problems), "Delivery today" binary sensor
  and "Next delivery" sensor.
- Delivery calendar with the expected delivery window of every incoming parcel.
- `boxblip_parcel_updated` event on every status change and `boxblip.refresh` action.
- Optional instant updates through a signed webhook (Home Assistant Cloud or a public https address).
- Ready-made dashboard in `dashboards/boxblip.yaml`.
- Diagnostics with personal data redacted. English and Italian translations.
