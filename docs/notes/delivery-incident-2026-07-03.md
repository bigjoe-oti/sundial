# Delivery incident, 2026-07-03

Notifier applet built and ran clean; every banner still failed silently.
Three stacked killers, each masking the next:

1. **No `CFBundleIdentifier`.** `osacompile`'s `Info.plist` ships with no
   bundle ID. Notification Center registers senders by bundle ID, so the
   applet could never register — `display notification` "succeeded" into
   the void, no error anywhere.
2. **`CFBundleIconName` shadowed `CFBundleIconFile`.** The asset-catalog
   key `osacompile` emits outranks the classic icon-file key, so a custom
   `applet.icns` in `Resources/` was silently ignored.
3. **Mirroring/sharing suppression.** "Allow notifications when mirroring
   or sharing" defaults off, blocking all banners on external displays
   with no per-app indication anything was suppressed.

Fix (in `setup.sh`, after `osacompile`): set `CFBundleIdentifier`, force
`CFBundleIconFile` and strip `CFBundleIconName`, rebuild `.icns`, re-sign,
re-register via `lsregister -f`. The owner must still click Allow on the
one-time prompt, enable the mirroring toggle, and set style to Alerts.
