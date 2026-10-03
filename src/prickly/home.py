"""The simulated house and the tools Needle may call on it.

Each public method on HouseState listed in TOOL_NAMES is a tool. Needle's schema
builder reads the type hints and docstrings, skips `self`, and turns `Literal`
and `Field` bounds into a grammar, so the model can only emit rooms, doors and
values that exist.

Needle withholds a call (`suppressed_calls`) when a numeric argument is not
grounded in what the user said, so "turn on the kitchen" needs its own tool
rather than `set_light_brightness(kitchen, 100)` with an invented 100.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Annotated, Any, Literal, get_args

import needle

Room = Literal["kitchen", "living_room", "bedroom", "bathroom", "office"]
Door = Literal["front", "back", "garage"]
Color = Literal["warm", "white", "cool", "red", "orange", "green", "blue", "purple", "pink"]

ROOMS: tuple[Room, ...] = get_args(Room)
DOORS: tuple[Door, ...] = get_args(Door)

Percent = Annotated[int, needle.Field(ge=0, le=100)]
Fahrenheit = Annotated[int, needle.Field(ge=50, le=90)]

DIM_LEVEL = 30


@dataclass
class Light:
    brightness: int = 0
    color: Color = "warm"


def _lights() -> dict[Room, Light]:
    return {room: Light() for room in ROOMS}


def _blinds() -> dict[Room, int]:
    return dict.fromkeys(ROOMS, 0)


def _locks() -> dict[Door, bool]:
    return dict.fromkeys(DOORS, True)


@dataclass
class HouseState:
    lights: dict[Room, Light] = field(default_factory=_lights)
    blinds: dict[Room, int] = field(default_factory=_blinds)
    locks: dict[Door, bool] = field(default_factory=_locks)
    thermostat_f: int = 70

    def turn_on_light(self, room: Room) -> dict[str, Any]:
        """Turn on the light in a room at full brightness.

        Args:
            room: the room to light
        """
        self.lights[room].brightness = 100
        return {"room": room, "brightness": 100}

    def turn_off_light(self, room: Room) -> dict[str, Any]:
        """Turn off the light in a room.

        Args:
            room: the room to darken
        """
        self.lights[room].brightness = 0
        return {"room": room, "brightness": 0}

    def dim_light(self, room: Room) -> dict[str, Any]:
        """Dim the light in a room when no level is given.

        Args:
            room: the room to dim
        """
        self.lights[room].brightness = DIM_LEVEL
        return {"room": room, "brightness": DIM_LEVEL}

    def set_light_brightness(self, room: Room, brightness: Percent) -> dict[str, Any]:
        """Set the light in a room to a specific brightness percentage.

        Args:
            room: the room whose light to change
            brightness: brightness percentage
        """
        self.lights[room].brightness = _clamp(brightness, 0, 100)
        return {"room": room, "brightness": self.lights[room].brightness}

    def set_light_color(self, room: Room, color: Color) -> dict[str, Any]:
        """Change the color of the light in a room.

        Args:
            room: the room whose light to change
            color: the new color
        """
        light = self.lights[room]
        light.color = color
        if light.brightness == 0:
            light.brightness = 100
        return {"room": room, "color": color, "brightness": light.brightness}

    def turn_off_all_lights(self) -> dict[str, Any]:
        """Turn off every light in the whole house."""
        for light in self.lights.values():
            light.brightness = 0
        return {"lights": "all off"}

    def set_thermostat(self, fahrenheit: Fahrenheit) -> dict[str, Any]:
        """Set the thermostat to a temperature.

        Args:
            fahrenheit: target temperature in degrees Fahrenheit
        """
        self.thermostat_f = _clamp(fahrenheit, 50, 90)
        return {"thermostat_f": self.thermostat_f}

    def lock_door(self, door: Door) -> dict[str, Any]:
        """Lock a door.

        Args:
            door: the door to lock
        """
        self.locks[door] = True
        return {"door": door, "locked": True}

    def unlock_door(self, door: Door) -> dict[str, Any]:
        """Unlock a door.

        Args:
            door: the door to unlock
        """
        self.locks[door] = False
        return {"door": door, "locked": False}

    def open_blinds(self, room: Room) -> dict[str, Any]:
        """Fully open the window blinds in a room.

        Args:
            room: the room whose blinds to open
        """
        self.blinds[room] = 100
        return {"room": room, "percent_open": 100}

    def close_blinds(self, room: Room) -> dict[str, Any]:
        """Fully close the window blinds in a room.

        Args:
            room: the room whose blinds to close
        """
        self.blinds[room] = 0
        return {"room": room, "percent_open": 0}

    def set_blinds(self, room: Room, percent_open: Percent) -> dict[str, Any]:
        """Open the window blinds in a room part of the way.

        Args:
            room: the room whose blinds to move
            percent_open: how far open, as a percentage
        """
        self.blinds[room] = _clamp(percent_open, 0, 100)
        return {"room": room, "percent_open": self.blinds[room]}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TOOL_NAMES = (
    "turn_on_light",
    "turn_off_light",
    "dim_light",
    "set_light_brightness",
    "set_light_color",
    "turn_off_all_lights",
    "set_thermostat",
    "lock_door",
    "unlock_door",
    "open_blinds",
    "close_blinds",
    "set_blinds",
)

TOOL_SCHEMAS: list[dict[str, Any]] = [
    needle.build_schema(getattr(HouseState, name)) for name in TOOL_NAMES
]

# Whistle keyword biasing: raises the log probability of these phrases while decoding.
KEYWORDS = ["thermostat", "blinds", "garage", "living room", "kitchen", "bedroom", "office"]

# Longer prompts with rules ("dim means 30") measurably hurt accuracy and confidence.
SYSTEM_PROMPT = (
    "Rooms: kitchen, living room, bedroom, bathroom, office. Doors: front, back, garage."
)


def apply_call(house: HouseState, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one tool call against the house, returning its result or an error."""
    if name not in TOOL_NAMES:
        return {"error": f"unknown tool: {name}"}
    try:
        return getattr(house, name)(**arguments)
    except (TypeError, KeyError, ValueError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))
