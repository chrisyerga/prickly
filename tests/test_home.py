from prickly.home import TOOL_NAMES, TOOL_SCHEMAS, HouseState, apply_call


def test_schemas_cover_every_tool_without_self():
    assert [s["name"] for s in TOOL_SCHEMAS] == list(TOOL_NAMES)
    for schema in TOOL_SCHEMAS:
        assert "self" not in schema["parameters"]["properties"]
        assert schema.get("description")


def test_room_and_door_arguments_are_enums():
    by_name = {s["name"]: s for s in TOOL_SCHEMAS}
    room = by_name["turn_on_light"]["parameters"]["properties"]["room"]
    assert room["enum"] == ["kitchen", "living_room", "bedroom", "bathroom", "office"]
    door = by_name["lock_door"]["parameters"]["properties"]["door"]
    assert door["enum"] == ["front", "back", "garage"]
    brightness = by_name["set_light_brightness"]["parameters"]["properties"]["brightness"]
    assert (brightness["minimum"], brightness["maximum"]) == (0, 100)


def test_lights():
    house = HouseState()
    house.turn_on_light("kitchen")
    assert house.lights["kitchen"].brightness == 100
    house.dim_light("kitchen")
    assert house.lights["kitchen"].brightness == 30
    house.set_light_brightness("kitchen", 140)
    assert house.lights["kitchen"].brightness == 100
    house.turn_off_light("kitchen")
    assert house.lights["kitchen"].brightness == 0


def test_color_turns_a_dark_light_on():
    house = HouseState()
    house.set_light_color("bedroom", "purple")
    assert house.lights["bedroom"].color == "purple"
    assert house.lights["bedroom"].brightness == 100


def test_all_off():
    house = HouseState()
    for room in house.lights:
        house.turn_on_light(room)
    house.turn_off_all_lights()
    assert all(light.brightness == 0 for light in house.lights.values())


def test_locks_blinds_thermostat():
    house = HouseState()
    assert house.locks["front"] is True
    house.unlock_door("front")
    assert house.locks["front"] is False
    house.set_blinds("office", 50)
    assert house.blinds["office"] == 50
    house.open_blinds("office")
    assert house.blinds["office"] == 100
    house.set_thermostat(40)
    assert house.thermostat_f == 50


def test_apply_call_reports_errors_instead_of_raising():
    house = HouseState()
    assert apply_call(house, "launch_rocket", {}) == {"error": "unknown tool: launch_rocket"}
    assert "error" in apply_call(house, "turn_on_light", {"room": "garage"})
    assert "error" in apply_call(house, "turn_on_light", {})
    assert apply_call(house, "lock_door", {"door": "back"}) == {"door": "back", "locked": True}
