"""Private scan storage, page previews and real Google Cloud Vision OCR."""
import io
import json
import warnings
from pathlib import Path

from PIL import Image, ImageOps


class ScanError(ValueError):
    pass


def save_scan(settings, document_id, content):
    """Validate bytes and create stable PNG previews with zero-based page indices."""
    directory = settings.storage_dir / document_id
    directory.mkdir(parents=True, exist_ok=False)
    pages = []
    try:
        if content.startswith(b"%PDF-"):
            import fitz
            with fitz.open(stream=content, filetype="pdf") as pdf:
                if pdf.needs_pass or not 1 <= len(pdf) <= settings.max_pages:
                    raise ScanError("PDF must be unencrypted and within the page limit")
                for index, page in enumerate(pdf):
                    # Limit raster dimensions even for a malicious oversized PDF page.
                    scale = min(2.0, 2400 / max(page.rect.width, page.rect.height))
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    pixmap.save(str(directory / f"page-{index}.png"))
                    pages.append({"page": index, "width": pixmap.width, "height": pixmap.height})
            mime = "application/pdf"
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    if image.format not in {"PNG", "JPEG"} or image.width * image.height > 25_000_000:
                        raise ScanError("Use a JPEG or PNG image with at most 25 million pixels")
                    image.load()
                    canonical = ImageOps.exif_transpose(image).convert("RGB")
                    canonical.save(directory / "page-0.png")
                    pages.append({"page": 0, "width": canonical.width, "height": canonical.height})
            mime = "image/png"
        (directory / "original").write_bytes(content)
        return mime, pages
    except Exception:
        # Only remove the exact files created in this new, UUID-named directory.
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()
        raise


def _normalise(annotation, page_offset=0):
    """Turn Google's structured response into viewer-friendly word polygons."""
    full = annotation.get("fullTextAnnotation", annotation.get("full_text_annotation", {}))
    pages = []
    for number, page in enumerate(full.get("pages", [])):
        words = []
        for block in page.get("blocks", []):
            for paragraph in block.get("paragraphs", []):
                for word in paragraph.get("words", []):
                    box = word.get("boundingBox", word.get("bounding_box", {}))
                    normal = box.get("normalizedVertices", box.get("normalized_vertices"))
                    if normal is None:
                        normal = [{"x": v.get("x", 0) / max(page.get("width", 1), 1),
                                   "y": v.get("y", 0) / max(page.get("height", 1), 1)} for v in box.get("vertices", [])]
                    words.append({"text": "".join(s.get("text", "") for s in word.get("symbols", [])),
                                  "polygon": normal, "confidence": word.get("confidence")})
        pages.append({"page": page_offset + number, "words": words, "confidence": page.get("confidence")})
    return {"text": full.get("text", ""), "pages": pages}


class GoogleOCR:
    def __init__(self, settings):
        self.settings = settings

    def extract(self, document, job_payload, checkpoint):
        """Checkpoint a PDF operation before waiting, so interrupted jobs can resume."""
        from google.cloud import vision
        from google.protobuf.json_format import MessageToDict

        client = vision.ImageAnnotatorClient()
        if document.mime != "application/pdf":
            content = (self.settings.storage_dir / document.id / "page-0.png").read_bytes()
            response = client.document_text_detection(image=vision.Image(content=content), timeout=120)
            if response.error.message:
                raise ScanError("Google OCR rejected the image; check its format and cloud configuration")
            raw = MessageToDict(response._pb)
            return {**_normalise(raw), "raw": raw, "provider": "google-cloud-vision"}

        if not self.settings.gcs_bucket:
            raise ScanError("Configure BACKEND_GCS_BUCKET for asynchronous PDF OCR")
        from google.api_core.operation import from_gapic
        from google.cloud import storage

        bucket = storage.Client().bucket(self.settings.gcs_bucket)
        prefix = job_payload["output_prefix"]
        operation_name = job_payload.get("operation_name")
        if operation_name:
            operations = client.transport.operations_client
            operation = from_gapic(operations.get_operation(operation_name), operations, vision.AsyncBatchAnnotateFilesResponse)
        else:
            input_key = f"economics/{document.id}/original.pdf"
            bucket.blob(input_key).upload_from_filename(str(self.settings.storage_dir / document.id / "original"), content_type="application/pdf")
            request = vision.AsyncAnnotateFileRequest(
                features=[vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)],
                input_config=vision.InputConfig(gcs_source=vision.GcsSource(uri=f"gs://{bucket.name}/{input_key}"), mime_type="application/pdf"),
                output_config=vision.OutputConfig(gcs_destination=vision.GcsDestination(uri=f"gs://{bucket.name}/{prefix}"), batch_size=1))
            operation = client.async_batch_annotate_files(requests=[request], timeout=30)
            operation_name = operation.operation.name
            checkpoint({"operation_name": operation_name})
        operation.result(timeout=600)
        responses = []
        for blob in bucket.list_blobs(prefix=prefix):
            if blob.name.endswith(".json"):
                responses.extend(json.loads(blob.download_as_bytes()).get("responses", []))
        responses.sort(key=lambda r: r.get("context", {}).get("pageNumber", 0))
        page_numbers = [r.get("context", {}).get("pageNumber") for r in responses]
        if page_numbers != list(range(1, len(document.pages) + 1)) or any(r.get("error") for r in responses):
            raise ScanError("OCR did not resolve every page; review or re-upload the document")
        text, pages = [], []
        for index, response in enumerate(responses):
            result = _normalise(response, index)
            text.append(result["text"])
            pages.extend(result["pages"])
        return {"text": "\n\n".join(text), "pages": pages, "raw": responses,
                "provider": "google-cloud-vision", "operation_name": operation_name}
