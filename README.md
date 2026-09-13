# Local TTS — Home Assistant custom integration

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Mugga6315&repository=ha-local-tts&category=integration)

A native HA TTS entity that is a thin client of the **TTS service** (the tts-ui /
prod gateway). It picks a named **prod entry** as the voice and may override any
of its params per call; the service shapes the backend request, resolves
reference clips, and forwards to llama-swap. No backend knowledge lives in HA.

## How it works

- **Voices = prod entries.** The service exposes `GET /api/prod` — tuned,
  named configurations (backend + params + reference clip) curated in the
  tts-ui. Each becomes a selectable voice in the Assist pipeline.
- **Synthesis.** HA sends `POST /v1/audio/speech` with the prod-entry id as
  `voice`, plus any param overrides in `extra_params`. The service does the
  backend-specific shaping (higgs control tokens, voxcpm knobs, qwen3 named
  voice) and resolves clone reference clips server-side — clips never travel to
  HA.
- **Overrides.** Any param advertised by the service's backends can be set per
  call via `tts.speak` `options:` (or left at the entry's tuned value).

## Streaming

Implements `async_stream_tts_audio`: incoming LLM text is split into sentences,
each synthesized as a complete WAV via the gateway, re-emitted as one continuous
WAV stream — first audio after the first sentence, not the whole answer. Falls
back to the 1-shot path if HA does not stream input.

## Setup

One config entry: the **service URL** (e.g. `http://homeassistant.local:8100`) and an
optional API key. The catalog of voices refreshes periodically, so entries
promoted in the tts-ui appear without re-adding the integration.

## Legacy

`custom_components/local_tts/_legacy/` holds the integration's first design,
where HA built backend payloads itself. Unused and not loaded — kept in case a
direct-to-backend path (no service in front) is ever wanted again.

## Acknowledgements

The voice-profiles-as-pipeline-voices pattern was inspired by
[HA-ElevenLabs-Custom-TTS](https://github.com/loryanstrant/HA-ElevenLabs-Custom-TTS)
by [@loryanstrant](https://github.com/loryanstrant). Thanks!
