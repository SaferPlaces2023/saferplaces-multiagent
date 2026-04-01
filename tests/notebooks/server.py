from flask import Flask, Response, stream_with_context
import time

app = Flask(__name__)

def genera_elementi():
    """Generator: yield ogni elemento non appena è pronto."""
    items = []
    for i in range(1, 6):
        time.sleep(2)                   # simula elaborazione
        items.append(f"item_{i}")
        # formato SSE: "data: ...\n\n"
        print(i)
        yield f"data: item_{i}\n\n"
    yield "data: [DONE]\n\n"            # segnale di fine

@app.route("/stream")
def stream():
    return Response(
        stream_with_context(genera_elementi()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # importante se usi nginx
        }
    )

if __name__ == "__main__":
    app.run(debug=True)