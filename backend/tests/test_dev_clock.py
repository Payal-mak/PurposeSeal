from datetime import datetime


def test_get_clock_returns_current_simulated_time(client):
    resp = client.get("/dev/clock")
    assert resp.status_code == 200
    # Just needs to parse as a valid ISO datetime.
    datetime.fromisoformat(resp.json()["now"].replace("Z", "+00:00"))


def test_advance_clock_moves_time_forward(client):
    before = client.get("/dev/clock").json()["now"]

    resp = client.post("/dev/clock/advance", json={"minutes": 45})
    assert resp.status_code == 200
    after = resp.json()["now"]

    before_dt = datetime.fromisoformat(before.replace("Z", "+00:00"))
    after_dt = datetime.fromisoformat(after.replace("Z", "+00:00"))
    delta_minutes = (after_dt - before_dt).total_seconds() / 60
    assert delta_minutes >= 44.9  # allow for tiny real-time drift between the two calls


def test_advance_clock_defaults_to_no_change_when_omitted(client):
    before = client.get("/dev/clock").json()["now"]
    resp = client.post("/dev/clock/advance", json={})
    assert resp.status_code == 200
    after = resp.json()["now"]

    before_dt = datetime.fromisoformat(before.replace("Z", "+00:00"))
    after_dt = datetime.fromisoformat(after.replace("Z", "+00:00"))
    assert (after_dt - before_dt).total_seconds() < 1  # only real time elapsed, no simulated jump
