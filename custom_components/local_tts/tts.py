"""Local TTS entity — a thin client of the TTS service (prod gateway).

Voices are the service's prod entries: named, tuned configurations (backend +
params + reference clip) curated in the tts-ui. HA picks one by id and may
override any of its params per call; the service shapes the backend request and
resolves reference clips. Streaming: incoming LLM text is split into sentences;
each sentence's PCM streams from the gateway as the engine emits it (the next
sentence synthesizing meanwhile) and is re-emitted as one continuous WAV stream,
so first audio arrives after the first chunk of the first sentence rather than
after the whole answer. Markdown is stripped before text reaches the engine.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, AsyncGenerator

from homeassistant.components.tts import (
    TextToSpeechEntity,
    TtsAudioType,
    TTSAudioRequest,
    TTSAudioResponse,
    Voice,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import client
from .audio import prefetched, sentences, speakable, stream_header
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    LANGUAGES,
    LOGGER,
    OPT_OVERRIDES,
    PREFETCH_DEPTH,
    REFRESH_INTERVAL,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([LocalTTSEntity(hass, entry)])


class LocalTTSEntity(TextToSpeechEntity):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._base_url: str = entry.data[CONF_BASE_URL]
        self._api_key: str = entry.data.get(CONF_API_KEY, "")
        self._attr_name = entry.title or "Local TTS"
        self._attr_unique_id = entry.entry_id
        # Cached prod catalog, refreshed from the service so new entries appear.
        self._voices: list[Voice] = []
        self._param_names: list[str] = []

    # --- catalog refresh ---
    async def async_added_to_hass(self) -> None:
        await self._refresh()
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._refresh, timedelta(seconds=REFRESH_INTERVAL)
            )
        )

    async def _refresh(self, _now=None) -> None:
        session = async_get_clientsession(self.hass)
        data = await client.fetch_prod(session, self._base_url, self._api_key)
        entries = data.get("entries", [])
        self._voices = [Voice(voice_id=e["id"], name=e.get("label", e["id"])) for e in entries]
        # Union of every backend's param names — the options a call may override.
        names: set[str] = set()
        for spec in data.get("backends", {}).values():
            for param in spec.get("params", []):
                names.add(param["name"])
        self._param_names = sorted(names)

    # --- HA TTS properties ---
    @property
    def default_language(self) -> str:
        return "de"

    @property
    def supported_languages(self) -> list[str]:
        return LANGUAGES

    @property
    def supported_options(self) -> list[str]:
        # `voice` picks a prod entry; each backend param is overridable per call.
        return ["voice", *self._param_names]

    @callback
    def async_get_supported_voices(self, language: str) -> list[Voice] | None:
        return self._voices or None

    # --- request building ---
    def _body(self, text: str, options: dict[str, Any] | None) -> dict[str, Any]:
        options = options or {}
        voice = options.get("voice") or (self._voices[0].voice_id if self._voices else "")
        # Saved per-entry overrides (options flow) first, per-call options win.
        saved = self._entry.options.get(OPT_OVERRIDES, {}).get(voice, {})
        percall = {k: v for k, v in options.items() if k != "voice" and v not in (None, "")}
        overrides = {**saved, **percall}
        body: dict[str, Any] = {"input": speakable(text), "voice": voice, "response_format": "wav"}
        if overrides:
            body["extra_params"] = overrides
        return body

    async def _synth_wav(self, text: str, options: dict[str, Any] | None) -> bytes:
        session = async_get_clientsession(self.hass)
        return await client.synth(session, self._base_url, self._api_key, self._body(text, options))

    # --- 1-shot (mandatory) ---
    async def async_get_tts_audio(
        self, message: str, language: str, options: dict[str, Any] | None = None
    ) -> TtsAudioType:
        try:
            wav = await self._synth_wav(message, options)
        except Exception as err:
            LOGGER.error("TTS synth failed: %s", err)
            return None, None
        return "wav", wav

    # --- streaming ---
    def async_supports_streaming_input(self) -> bool:
        return True

    async def async_stream_tts_audio(
        self, request: TTSAudioRequest
    ) -> TTSAudioResponse:
        return TTSAudioResponse(
            extension="wav",
            data_gen=self._stream(request.options, request.message_gen),
        )

    async def _stream(
        self, options: dict[str, Any], message_gen: AsyncGenerator[str],
    ) -> AsyncGenerator[bytes]:
        # Split the incoming LLM text into sentences and stream each sentence's
        # PCM from the gateway as the engine emits it — one continuous WAV: a
        # header once, then raw 16-bit-mono frames. First audio arrives after the
        # first chunk of the first sentence, not after the whole answer. The next
        # sentence synthesizes while the current one streams, so its first-chunk
        # latency does not become a gap between sentences.
        header_sent = False
        async for item in prefetched(
            sentences(message_gen), lambda s: self._synth_sentence(s, options),
            depth=PREFETCH_DEPTH,
        ):
            if isinstance(item, int):  # each sentence stream opens with its sample rate
                if not header_sent:
                    yield stream_header(item, 2, 1)
                    header_sent = True
                continue
            yield item

    async def _synth_sentence(
        self, sentence: str, options: dict[str, Any]
    ) -> AsyncGenerator[int | bytes]:
        """One sentence as sample rate then PCM chunks. A failed sentence is
        logged and skipped so the rest of the answer still plays; a sentence
        that is only Markdown markup has nothing to say and is skipped."""
        if not speakable(sentence):
            return
        session = async_get_clientsession(self.hass)
        body = self._body(sentence, options)
        try:
            async for item in client.synth_stream(session, self._base_url, self._api_key, body):
                yield item
        except Exception as err:
            LOGGER.error("TTS stream synth failed on %r: %s", sentence[:40], err)
