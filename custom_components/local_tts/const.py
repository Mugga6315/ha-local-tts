from __future__ import annotations

import logging

DOMAIN = "local_tts"
LOGGER = logging.getLogger(__package__)

# Config-entry data: just where the TTS service lives. All backend knowledge
# (params, refs, payload shaping) is server-side now — this integration is a
# thin client that picks a named prod entry and optionally overrides its params.
CONF_BASE_URL = "base_url"        # TTS-service root, e.g. http://homeassistant.local:8100
CONF_API_KEY = "api_key"          # optional bearer (local service usually none)

# Config-entry options: per-prod-entry saved param overrides, set in the options
# flow, merged under any per-call options at synth time.
#   options = {OPT_OVERRIDES: {<prod-entry id>: {<param>: <value>}}}
OPT_OVERRIDES = "overrides"

# Languages advertised to the Assist pipeline (the German+English assistant).
LANGUAGES = ["de", "en"]

DEFAULT_TIMEOUT = 300             # per-request ceiling; a long answer can take a while
REFRESH_INTERVAL = 60             # seconds between prod-entry re-fetches (picks up new ones)
# Sentences synthesized at once while streaming: the one playing + the next.
# Needs the backend to serve that many requests in parallel (vLLM --max-num-seqs).
PREFETCH_DEPTH = 2
