"""Backend registry — one entry per TTS server, all speaking OpenAI /v1/audio/speech.

Adding a backend = append a Backend here. The entity and config flow read this
table generically; nothing else changes. Every backend here is served either by
faster-qwen3-tts or by vLLM-Omni, both of which expose POST /v1/audio/speech.

A voice *profile* (configured in the options flow) is a dict of this backend's
param values plus a `mode`. `build_payload` turns a profile + one text chunk into
the request body. Param names/values mirror ~/voice/tts/model-workers/tts_caps.py
and the body mapping mirrors tts_worker.py's OmniProxyAdapter, kept in sync by hand
(this file ships to the HA box and cannot import that repo).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# Languages the German+English assistant uses. Backends support more; these are
# what we advertise to the Assist pipeline.
LANGUAGES = ["de", "en"]


@dataclass
class Param:
    name: str
    type: str                      # int | float | bool | choice | str
    default: Any
    label: str
    choices: list[str] | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None


@dataclass
class Mode:
    id: str
    label: str
    needs: list[str] = field(default_factory=list)  # ref_audio | ref_text | voice | design


@dataclass
class Backend:
    id: str
    label: str
    default_base_url: str
    default_model: str
    modes: list[Mode]
    params: list[Param]
    build_payload: Callable[..., dict]
    voices: list[str] = field(default_factory=list)

    def param(self, name: str) -> Param | None:
        return next((p for p in self.params if p.name == name), None)

    def merged(self, profile: dict) -> dict:
        """Profile values over this backend's defaults, coerced to declared types."""
        out = {p.name: p.default for p in self.params}
        coerce = {"int": int, "float": float, "bool": bool}
        for p in self.params:
            if p.name in profile and profile[p.name] not in (None, ""):
                out[p.name] = coerce.get(p.type, str)(profile[p.name])
        return out


# --- Higgs (vLLM-Omni) --------------------------------------------------------
# Delivery dropdowns become inline control tokens prepended to the text; the rest
# ride the OpenAI body. Mirrors HiggsAdapter._prefix / synth in tts_worker.py.
_HIGGS_PACE = {"very slow": "speed_very_slow", "slow": "speed_slow",
               "fast": "speed_fast", "very fast": "speed_very_fast"}
_HIGGS_PITCH = {"lower": "pitch_low", "higher": "pitch_high"}
_HIGGS_EXPR = {"more": "expressive_high", "flatter": "expressive_low"}


def _higgs_prefix(o: dict) -> str:
    out = ""
    if o.get("emotion") and o["emotion"] != "none":
        out += f"<|emotion:{o['emotion']}|>"
    if o.get("style") and o["style"] != "none":
        out += f"<|style:{o['style']}|>"
    for tok in (_HIGGS_PACE.get(o.get("pace") or ""),
                _HIGGS_PITCH.get(o.get("pitch") or ""),
                _HIGGS_EXPR.get(o.get("expressiveness") or "")):
        if tok:
            out += f"<|prosody:{tok}|>"
    return out


def _higgs_payload(backend, model, text, opts, language, mode, ref_data_url, ref_text):
    payload: dict = {"model": model, "input": _higgs_prefix(opts) + text,
                     "response_format": "wav"}
    if opts.get("max_new_tokens"):
        payload["max_new_tokens"] = int(opts["max_new_tokens"])
    if opts.get("temperature") is not None:     # not a top-level field -> extra_params
        payload["extra_params"] = {"temperature": float(opts["temperature"])}
    if mode == "clone" and ref_data_url:
        payload["ref_audio"] = ref_data_url
        payload["ref_text"] = ref_text or ""
    return payload


# --- VoxCPM2 (vLLM-Omni) ------------------------------------------------------
# Generation knobs ride extra_params; clone/design carried per the omni schema.
def _voxcpm_payload(backend, model, text, opts, language, mode, ref_data_url, ref_text):
    extra = {k: opts[k] for k in ("cfg_value", "inference_timesteps", "normalize")
             if opts.get(k) is not None}
    payload: dict = {"model": model, "input": text, "response_format": "wav"}
    if extra:
        payload["extra_params"] = extra
    if mode in ("clone", "ultra") and ref_data_url:
        payload["ref_audio"] = ref_data_url
        if mode == "ultra":
            payload["ref_text"] = ref_text or ""
    return payload


# --- Qwen3-TTS (faster-qwen3-tts) --------------------------------------------
# Production path today. Preset voices carry timbre+language; clone via ref fields.
def _qwen3_payload(backend, model, text, opts, language, mode, ref_data_url, ref_text):
    payload: dict = {"model": model, "input": text, "response_format": "wav"}
    if opts.get("voice"):
        payload["voice"] = opts["voice"]
    if opts.get("speed") is not None:
        payload["speed"] = float(opts["speed"])
    if opts.get("instruct"):                     # CustomVoice style/emotion cue
        payload["instruct"] = opts["instruct"]
    if mode == "clone" and ref_data_url:
        payload["ref_audio"] = ref_data_url
        payload["ref_text"] = ref_text or ""
    return payload


BACKENDS: dict[str, Backend] = {
    "qwen3": Backend(
        id="qwen3",
        label="Qwen3-TTS (faster-qwen3-tts)",
        default_base_url="http://homeassistant.local:8080/v1",
        default_model="qwen3-tts-faster",
        voices=["ryan_de", "chicago_de"],
        modes=[
            Mode("preset", "preset voice", ["voice"]),
            Mode("clone", "clone — audio + transcript", ["ref_audio", "ref_text"]),
        ],
        params=[
            Param("voice", "choice", "chicago_de", "Preset voice",
                  choices=["ryan_de", "chicago_de"]),
            Param("speed", "float", 1.0, "Speed", min=0.5, max=2.0, step=0.05),
            Param("instruct", "str", "", "Style/emotion cue (CustomVoice)"),
        ],
        build_payload=_qwen3_payload,
    ),
    "higgs": Backend(
        id="higgs",
        label="Higgs V3 (vLLM-Omni)",
        default_base_url="http://homeassistant.local:8080/v1",
        default_model="higgs-tts",
        modes=[
            Mode("plain", "plain — just read the text", []),
            Mode("clone", "clone — copy a voice from a sample", ["ref_audio", "ref_text"]),
        ],
        params=[
            Param("emotion", "choice", "none", "Emotion",
                  choices=["none", "elation", "amusement", "enthusiasm", "determination",
                           "pride", "contentment", "affection", "relief", "contemplation",
                           "confusion", "surprise", "awe", "longing", "anger", "fear",
                           "disgust", "bitterness", "sadness", "shame", "helplessness"]),
            Param("style", "choice", "none", "Style",
                  choices=["none", "whispering", "shouting", "singing"]),
            Param("pace", "choice", "normal", "Pace",
                  choices=["normal", "very slow", "slow", "fast", "very fast"]),
            Param("pitch", "choice", "normal", "Pitch",
                  choices=["normal", "lower", "higher"]),
            Param("expressiveness", "choice", "normal", "Expressiveness",
                  choices=["normal", "more", "flatter"]),
            Param("temperature", "float", 0.8, "Temperature", min=0.1, max=2.0, step=0.05),
            Param("max_new_tokens", "int", 1024, "Max audio tokens", min=64, max=4096, step=64),
        ],
        build_payload=_higgs_payload,
    ),
    "voxcpm": Backend(
        id="voxcpm",
        label="VoxCPM2 (vLLM-Omni)",
        default_base_url="http://homeassistant.local:8080/v1",
        default_model="voxcpm-tts",
        modes=[
            Mode("plain", "plain — text only", []),
            Mode("design", "design — describe the voice", ["design"]),
            Mode("clone", "clone — reference audio", ["ref_audio"]),
            Mode("ultra", "ultimate clone — audio + transcript", ["ref_audio", "ref_text"]),
        ],
        params=[
            Param("cfg_value", "float", 2.0, "CFG strength", min=0.5, max=5.0, step=0.1),
            Param("inference_timesteps", "int", 10, "Denoising steps", min=4, max=50, step=1),
            Param("normalize", "bool", False, "Normalize numbers/dates"),
        ],
        build_payload=_voxcpm_payload,
    ),
}
