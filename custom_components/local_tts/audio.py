"""Pure audio/text helpers for the streaming path — no Home Assistant imports,
so they are unit-testable on their own (tests/test_audio.py)."""

from __future__ import annotations

import asyncio
import io
import re
import wave
from typing import Any, AsyncGenerator, Callable

# End of a sentence: run up to .!?… (plus trailing quotes/brackets) before a
# space or end, OR a newline. Keeps the delimiter with the sentence.
_SENTENCE_END = re.compile(r"[^.!?…\n]*(?:[.!?…]+[\"')\]]*(?=\s|$)|\n)", re.DOTALL)


# Markdown the LLM writes that a TTS engine would try to pronounce. Only markers
# go: emphasis runs (** __ * `), a heading's leading #s, a list bullet at line
# start. A lone `_` inside a word (entity ids) is text, not markup.
_MARKDOWN = re.compile(r"\*+|`+|(?<!\w)__|__(?!\w)|^\s*#+\s*|^\s*[-•]\s+", re.MULTILINE)


def speakable(text: str) -> str:
    """Text with Markdown formatting removed, ready to send to the TTS engine."""
    return _MARKDOWN.sub("", text).strip()


async def sentences(message_gen: AsyncGenerator[str]) -> AsyncGenerator[str]:
    """Yield complete sentences as text arrives; flush the remainder at the end."""
    buf = ""
    async for chunk in message_gen:
        buf += chunk
        # Every match ends in a delimiter, so it always consumes text. A
        # whitespace-only match (the "\n\n" LLM replies start with) is dropped,
        # not treated as "no sentence yet", or splitting would stall until the
        # whole answer arrived.
        while m := _SENTENCE_END.match(buf):
            buf = buf[m.end():]
            if sentence := m.group().strip():
                yield sentence
    if buf.strip():
        yield buf.strip()


async def prefetched(
    items: AsyncGenerator[str],
    fetch: Callable[[str], AsyncGenerator[Any]],
    depth: int = 2,
) -> AsyncGenerator[Any]:
    """Yield everything fetch(item) yields, item by item in order, while up to
    `depth` fetches run at once — the next sentence synthesizes while the
    current one is still streaming out. Each fetch buffers into its own queue."""
    slots = asyncio.Semaphore(depth)
    jobs: asyncio.Queue = asyncio.Queue()
    tasks: set[asyncio.Task] = set()  # every task started here; all joined on exit
    end = object()

    async def pump(item: str, out: asyncio.Queue) -> None:
        try:
            async for value in fetch(item):
                await out.put(value)
        finally:
            await out.put(end)

    async def feed() -> None:
        try:
            async for item in items:
                await slots.acquire()
                out: asyncio.Queue = asyncio.Queue()
                task = asyncio.create_task(pump(item, out))
                tasks.add(task)
                await jobs.put((out, task))
        finally:
            await jobs.put(None)

    feeder = asyncio.create_task(feed())
    tasks.add(feeder)
    try:
        while (job := await jobs.get()) is not None:
            out, task = job
            while (value := await out.get()) is not end:
                yield value
            await task  # re-raises a fetch error; fetch decides what is fatal
            slots.release()
        await feeder  # re-raises an error from the text source
    finally:
        # Consumer stopped early (or failed): stop all in-flight synth requests.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def wav_parts(data: bytes) -> tuple[int, int, int, bytes] | None:
    """(sample_rate, sampwidth, channels, pcm_frames) from a WAV blob, or None."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            return (wf.getframerate(), wf.getsampwidth(), wf.getnchannels(),
                    wf.readframes(wf.getnframes()))
    except Exception:
        return None


def stream_header(sr: int, sampwidth: int, channels: int) -> bytes:
    """A WAV header with max-size placeholders for a stream of unknown length.
    ffmpeg `-f wav` (HA's converter) reads this from a pipe fine."""
    byte_rate = sr * channels * sampwidth
    block_align = channels * sampwidth
    return b"".join([
        b"RIFF", (0xFFFFFFFF).to_bytes(4, "little"), b"WAVE",
        b"fmt ", (16).to_bytes(4, "little"),
        (1).to_bytes(2, "little"), channels.to_bytes(2, "little"),
        sr.to_bytes(4, "little"), byte_rate.to_bytes(4, "little"),
        block_align.to_bytes(2, "little"), (sampwidth * 8).to_bytes(2, "little"),
        b"data", (0xFFFFFFFF).to_bytes(4, "little"),
    ])

