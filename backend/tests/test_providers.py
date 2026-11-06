import io
from PIL import Image
from backend.config import Settings

def image_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, "PNG")
    return buffer.getvalue()

def test_controller_enforces_two_edges_and_six_replies():
    from types import SimpleNamespace
    from backend.tutoring import TutorController, initial_data
    graph = {str(i): {"prereqs": [str(i+1)] if i < 3 else [],
                      "probes": [{"q": f"Check {i}.{j}", "answer": "Private"} for j in range(8)]} for i in range(4)}

    class BankRoles:
        def probe(self, target, eligible, used, remaining):
            kp = eligible[0]
            index = next(i for i in range(8) if [kp, i] not in used)
            return {"kp_id": kp, "probe_index": index, "question": graph[kp]["probes"][index]["q"], "stop_reason": None}

        def judge(self, active, reply):
            return {"verdict": "dont_know", "evidence_quote": reply, "explanation": "Not understood"}

        def teach(self, *args):
            return {"explanation": "Lesson", "worked_example": None, "check_question": "Unvalidated"}

    controller = TutorController(SimpleNamespace(graph=graph), BankRoles())
    data = initial_data({}, {})
    data["path"], data["visited"] = ["0"], ["0"]
    assert controller.select_probe(data) == "awaiting_probe_reply"
    for _ in range(6):
        state, data = controller.reply(data, "Not sure")
        assert len(data["path"]) <= 3
        assert "3" not in data["visited"]
    assert state == "awaiting_revision"
    assert data["reply_count"] == 6
    assert len(data["used"]) == 6
    assert data["active"] is None


def test_google_image_adapter_with_actual_sdk_types(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from google.cloud import vision
    from backend.ocr import GoogleOCR, save_scan
    settings = Settings(storage_dir=tmp_path)
    mime, pages = save_scan(settings, "document", image_bytes())
    response = vision.AnnotateImageResponse(full_text_annotation=vision.TextAnnotation(text="costs", pages=[
        vision.Page(width=100, height=100, blocks=[vision.Block(paragraphs=[vision.Paragraph(words=[
            vision.Word(symbols=[vision.Symbol(text=c) for c in "costs"],
                        bounding_box=vision.BoundingPoly(vertices=[vision.Vertex(x=10, y=20), vision.Vertex(x=40, y=30)]), confidence=0.8)
        ])])])]))

    class Client:
        def document_text_detection(self, *, image, timeout):
            assert image.content.startswith(b"\x89PNG")
            return response

    monkeypatch.setattr(vision, "ImageAnnotatorClient", Client)
    result = GoogleOCR(settings).extract(SimpleNamespace(id="document", mime=mime, pages=pages), {}, lambda _: None)
    assert result["text"] == "costs"
    word = result["pages"][0]["words"][0]
    assert word["polygon"][0] == {"x": 0.1, "y": 0.2}
    assert word["text"] == "costs"
