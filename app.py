import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from detector import analyze_llm, analyze_stylometric
from scorer import score_content
from storage import append_log, get_content, get_log, init_db, save_content, update_status

load_dotenv()

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

init_db()


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per hour")
def submit():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()
    creator_id = data.get("creator_id", "anonymous")

    if not text:
        return jsonify({"error": "text field is required"}), 400

    llm_score = analyze_llm(text)
    stylometric_score = analyze_stylometric(text)
    result = score_content(llm_score, stylometric_score)

    content_id = save_content(creator_id, text)

    log_entry = {
        "event_type": "classification",
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": result["attribution"],
        "confidence": result["confidence"],
        "ai_probability": result["ai_probability"],
        "llm_score": llm_score,
        "stylometric_score": stylometric_score,
        "transparency_label": result["transparency_label"],
        "status": "classified",
    }
    append_log(log_entry)

    return jsonify({
        "content_id": content_id,
        "attribution": result["attribution"],
        "confidence": result["confidence"],
        "ai_probability": result["ai_probability"],
        "signals": {
            "llm_score": llm_score,
            "stylometric_score": stylometric_score,
        },
        "transparency_label": result["transparency_label"],
        "status": "classified",
    })


@app.route("/appeal", methods=["POST"])
@limiter.limit("5 per hour")
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = data.get("content_id", "").strip()
    creator_reasoning = (
        data.get("creator_reasoning") or data.get("reason") or ""
    ).strip()

    if not content_id:
        return jsonify({"error": "content_id is required"}), 400
    if not creator_reasoning:
        return jsonify({"error": "creator_reasoning is required"}), 400

    content = get_content(content_id)
    if not content:
        return jsonify({"error": "content not found"}), 404
    if content["status"] == "under_review":
        return jsonify({"error": "An appeal is already pending for this content."}), 409

    update_status(content_id, "under_review")

    log_entry = {
        "event_type": "appeal",
        "content_id": content_id,
        "creator_id": content.get("creator_id"),
        "appeal_reasoning": creator_reasoning,
        "status": "under_review",
    }
    append_log(log_entry)

    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "message": "Your appeal has been submitted and is under review.",
    })


@app.route("/log", methods=["GET"])
def log():
    return jsonify({"entries": get_log()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
