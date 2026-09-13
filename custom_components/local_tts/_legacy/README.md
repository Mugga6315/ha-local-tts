# Legacy — unused

Code from the integration's first design, where HA built backend-specific
`/v1/audio/speech` payloads itself (per-backend params, inline control tokens,
base64 ref clips). Superseded by the server-side **prod gateway** in the
tts-service: HA now sends only a prod-entry id + optional param overrides, and
the service shapes the backend request and resolves reference clips.

Kept in case the direct-to-backend path is ever wanted again (e.g. an HA install
with no tts-service in front). Nothing imports this folder; HA does not load it
(underscore-prefixed, not a platform module).

- `backends.py` — the per-backend registry + `build_payload` functions.
