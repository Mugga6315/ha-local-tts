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

Implements `async_stream_tts_audio`: incoming LLM text is split into sentences
as it arrives, and each sentence's PCM streams from the gateway as the engine
emits it, re-emitted as one continuous WAV stream — first audio after the first
chunk of the first sentence, not after the whole answer. Falls back to the
1-shot path if HA does not stream input.

- **Prefetch.** The next sentence is synthesized while the current one streams
  (`PREFETCH_DEPTH = 2` in `const.py`), so its first-chunk latency does not
  become a pause between sentences. The backend must serve that many requests
  in parallel (vLLM `--max-num-seqs` ≥ 2); otherwise the prefetch simply waits
  its turn.
- **Markdown.** LLM replies often contain Markdown. Emphasis markers
  (`**`, `__`, `*`, backticks), heading `#`s and list bullets are stripped
  before the text reaches the engine, so they are not spoken.

## Setup

One config entry: the **service URL** (e.g. `http://homeassistant.local:8100`) and an
optional API key. The catalog of voices refreshes periodically, so entries
promoted in the tts-ui appear without re-adding the integration.

## Tests

```bash
uv run --with pytest pytest tests
```

## Legacy

`custom_components/local_tts/_legacy/` holds the integration's first design,
where HA built backend payloads itself. Unused and not loaded — kept in case a
direct-to-backend path (no service in front) is ever wanted again.

## Acknowledgements

The voice-profiles-as-pipeline-voices pattern was inspired by
[HA-ElevenLabs-Custom-TTS](https://github.com/loryanstrant/HA-ElevenLabs-Custom-TTS)
by [@loryanstrant](https://github.com/loryanstrant). Thanks!
