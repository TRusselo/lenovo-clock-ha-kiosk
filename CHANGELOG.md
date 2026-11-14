# Changelog

What changed for people converting a clock. Newest first.

## Unreleased

- First conversion, by hand, of one Lenovo Smart Clock gen 1: stock Android Things on slot A with offline-provisioned
  userdata (adb over Wi-Fi, RescueParty off, OTA channel blocked), Kiosk Satellite installed as device owner,
  Google Assistant, Cast and check-ins disabled.
- [Manual steps](devices/cube/README.md), each with its check.
- `lck` package: misc A/B and OTA-channel builder, sparse-image size check, userdata injector, with tests.
