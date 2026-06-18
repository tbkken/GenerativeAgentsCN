import os
from datetime import datetime

from flask import Flask, jsonify, render_template, request, url_for

from compress import file_movement, frames_per_step, generate_movement


app = Flask(
    __name__,
    template_folder="frontend/templates",
    static_folder="frontend/static",
    static_url_path="/static",
)


def _simulation_paths(name):
    checkpoints_folder = os.path.join("results", "checkpoints", name)
    live_folder = os.path.join("results", "live", name)
    return checkpoints_folder, live_folder


def _checkpoint_files(checkpoints_folder):
    if not os.path.isdir(checkpoints_folder):
        return []
    return [
        os.path.join(checkpoints_folder, file_name)
        for file_name in sorted(os.listdir(checkpoints_folder))
        if file_name.endswith(".json") and file_name != "conversation.json"
    ]


def _latest_step(json_files):
    if not json_files:
        return 0
    latest_name = os.path.basename(json_files[-1])
    # Checkpoint files are written after a whole step is complete, so mtime is
    # enough for the live UI to know whether new data arrived.
    return int(datetime.fromtimestamp(os.path.getmtime(json_files[-1])).timestamp())


def build_live_payload(name):
    checkpoints_folder, live_folder = _simulation_paths(name)
    json_files = _checkpoint_files(checkpoints_folder)
    if not json_files:
        return None

    os.makedirs(live_folder, exist_ok=True)
    payload = generate_movement(checkpoints_folder, live_folder, file_movement)
    payload["latest_checkpoint"] = os.path.basename(json_files[-1])
    payload["latest_checkpoint_mtime"] = _latest_step(json_files)
    payload["latest_frame"] = max(
        [int(k) for k in payload["all_movement"].keys() if k.isdigit()],
        default=0,
    )
    return payload


@app.route("/", methods=["GET"])
def index():
    name = request.args.get("name", "")
    step = int(request.args.get("step", 0))
    speed = int(request.args.get("speed", 2))
    zoom = float(request.args.get("zoom", 0.8))
    poll_ms = int(request.args.get("poll", 5000))

    if len(name) < 1:
        return "Invalid name of the simulation."

    payload = build_live_payload(name)
    if payload is None:
        checkpoints_folder, _ = _simulation_paths(name)
        return render_template(
            "live_wait.html",
            name=name,
            checkpoints_folder=checkpoints_folder,
            poll_ms=max(poll_ms, 1000),
        )

    if step < 1:
        step = 1
    if speed < 0:
        speed = 0
    elif speed > 5:
        speed = 5

    return render_template(
        "index.html",
        persona_names=list(payload["persona_init_pos"].keys()),
        step=step,
        play_speed=2 ** speed,
        zoom=zoom,
        live_config={
            "api_url": url_for("live_data", name=name),
            "poll_ms": max(poll_ms, 1000),
        },
        **payload,
    )


@app.route("/api/live/<name>", methods=["GET"])
def live_data(name):
    payload = build_live_payload(name)
    if payload is None:
        checkpoints_folder, _ = _simulation_paths(name)
        return jsonify(
            {
                "ready": False,
                "message": f"Waiting for checkpoints in {checkpoints_folder}",
            }
        ), 202
    return jsonify({"ready": True, **payload})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False)
