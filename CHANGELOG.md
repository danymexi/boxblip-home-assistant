# Changelog

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
