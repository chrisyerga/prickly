"""Run a fixed set of utterances through Needle to compare system prompts."""

import sys
import time

import needle

from prickly.home import TOOL_SCHEMAS

UTTERANCES = [
    "Dim the kitchen lights to 30% and lock the front door.",
    "turn on the living room lights",
    "make the bedroom purple",
    "set the thermostat to 68 and close the office blinds",
    "unlock the garage",
    "dim the bathroom",
    "turn everything off",
    "turn off the kitchen light",
    "order a pizza",
    "open the blinds halfway in the living room",
    "it's too dark in the office",
]

VARIANTS = {
    "none": None,
    "rooms": "Rooms: kitchen, living room, bedroom, bathroom, office. Doors: front, back, garage.",
}

for label in sys.argv[1:] or VARIANTS:
    agent = needle.Needle(tools=TOOL_SCHEMAS, system=VARIANTS[label], stateless=True)
    print(f"=== {label}")
    for q in UTTERANCES:
        t = time.perf_counter()
        r = agent.complete(q)
        ms = (time.perf_counter() - t) * 1000
        calls = [(c["name"], c["arguments"]) for c in r["function_calls"]]
        sup = [(c["name"], c["arguments"]) for c in r.get("suppressed_calls") or []]
        held = f"SUP {sup}" if sup else ""
        print(f"{ms:6.1f}ms {r.get('confidence'):.2f} {q!r:60} {calls} {held}")
    agent.close()
