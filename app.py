from flask import Flask, render_template, jsonify, request
from playback import PlaybackController
import atexit

app = Flask(__name__)

controller = PlaybackController()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/recordings")
def recordings():
    return jsonify(controller.list_recordings())

@app.route("/api/play/<filename>")
def play(filename):
    try:
        controller.play(filename)
        return jsonify({"success": True, "playing": filename})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/stop")
def stop():
    controller.stop()
    return jsonify({"success": True})

@app.route("/api/status")
def status():
    return jsonify(controller.get_status())

@app.route("/api/start")
def start():
    try:
        controller.start()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/reload")
def reload_recordings():
    return jsonify({
        "success": True,
        "recordings": controller.list_recordings()
    })



@app.route("/api/blackout")
def blackout():
    controller.blackout()
    return jsonify({"success": True})



@atexit.register
def shutdown():
    controller.shutdown()

if __name__ == "__main__":
    controller.start()
    app.run(host="0.0.0.0", port=80, threaded=True)