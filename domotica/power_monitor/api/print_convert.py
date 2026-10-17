# -*- coding: utf-8 -*-
from http.server import BaseHTTPRequestHandler
import json
import zlib
import io
import os
import base64
import re
import pymupdf
from PIL import Image, ImageOps, ImageDraw

HEARTBEAT_SECRET = os.getenv("HEARTBEAT_SECRET", "SecretHeartbeatKey2026")

W_PIX = 5100   # Letter 8.5 in @ 600 DPI
H_PIX = 6600   # Letter 11.0 in @ 600 DPI
NUM_STRIPS = 413  # 412 strisce da 16 linee + 1 striscia da 8 linee = 6600 linee


def build_exact_letter_pclm_canvas(canvas):
    """
    Costruisce lo stream PCLm da un canvas Letter (5100 x 6600, mode L).
    """
    raw_bytes = canvas.tobytes()
    bytes_per_row = W_PIX

    out = bytearray()
    offsets = {}
    out += b"%PDF-1.7\n%PCLm 1.0%\n"

    # 1. Page Object (416 0 obj)
    offsets[416] = len(out)
    out += b"416 0 obj\n<<\n/Type /Page\n/Parent 2 0 R\n/Resources <<\n/XObject <<\n"
    for i in range(NUM_STRIPS):
        out += f"/Image{i} {i + 3} 0 R\n".encode("latin1")
    out += b">>\n>>\n/MediaBox [ 0 0 612 792 ]\n/Contents [ 417 0 R ]\n>>\nendobj\n"

    # 2. Content Stream (417 0 obj)
    cs = bytearray()
    cs += b"0.120000 0.000000 0.000000 0.120000 0.000000 0.000000 cm\n"
    for i in range(NUM_STRIPS):
        curr_h = 8 if i == 412 else 16
        y_pos = H_PIX - (i + 1) * 16 if i < 412 else 0
        cs += f"q\n5100.000000 0.000000 0.000000 {curr_h:.6f} 0.000000 {y_pos:.6f} cm\n/Image{i} Do Q\n".encode("latin1")

    offsets[417] = len(out)
    out += f"417 0 obj\n<<\n/Length {len(cs)}\n>>\nstream\n".encode("latin1")
    out += cs
    out += b"endstream\nendobj\n"

    # 3. Image Strips (3 0 obj .. 415 0 obj)
    for i in range(NUM_STRIPS):
        obj_id = i + 3
        curr_h = 8 if i == 412 else 16
        y_start = i * 16
        y_end = y_start + curr_h
        strip_raw = raw_bytes[y_start * bytes_per_row : y_end * bytes_per_row]
        compressed = zlib.compress(strip_raw, level=9)

        offsets[obj_id] = len(out)
        out += f"{obj_id} 0 obj\n<<\n/Width 5100\n/ColorSpace /DeviceGray\n/Height {curr_h}\n/Filter /FlateDecode\n/Subtype /Image\n/Length {len(compressed)}\n/Type /XObject\n/BitsPerComponent 8\n>>\nstream\n".encode("latin1")
        out += compressed
        out += b"\nendstream\nendobj\n"

    # 4. Pages Object (2 0 obj)
    offsets[2] = len(out)
    out += b"2 0 obj\n<<\n/Count 1\n/Kids [ 416 0 R ]\n/Type /Pages\n>>\nendobj\n"

    # 5. Catalog Object (1 0 obj)
    offsets[1] = len(out)
    out += b"1 0 obj\n<<\n/Pages 2 0 R\n/Type /Catalog\n>>\nendobj\n"

    # 6. Xref Table
    xref_offset = len(out)
    out += b"xref\n0 1\r\n0000000000 65535 f\r\n416 2\r\n"
    out += f"{offsets[416]:010d} 00000 n\r\n".encode("latin1")
    out += f"{offsets[417]:010d} 00000 n\r\n".encode("latin1")
    out += b"3 413\r\n"
    for i in range(NUM_STRIPS):
        out += f"{offsets[i + 3]:010d} 00000 n\r\n".encode("latin1")
    out += b"2 1\r\n"
    out += f"{offsets[2]:010d} 00000 n\r\n".encode("latin1")
    out += b"1 1\r\n"
    out += f"{offsets[1]:010d} 00000 n\r\n".encode("latin1")

    # 7. Trailer
    out += f"trailer\n<<\n/Root 1 0 R\n/Size 418\n>>\nstartxref\n{xref_offset}\n%%EOF\n".encode("latin1")

    return bytes(out)


def render_image_on_canvas(pil_img, orientation="portrait", fit_mode="fit", position="center"):
    """
    Applica orientamento (portrait/landscape), adattamento (fit/fill/original)
    e posizionamento (center/top/bottom) su canvas Letter 5100x6600.
    """
    img = pil_img.copy()

    # 1. Orientamento
    if orientation == "landscape":
        # Ruota di 90 gradi per orientamento orizzontale
        img = img.rotate(90, expand=True)

    # 2. Adattamento (Scale)
    # Margine di sicurezza di 150 pixel (~6 mm a 600 DPI) per non tagliare bordi stampabili
    avail_w = W_PIX - 300
    avail_h = H_PIX - 300

    if fit_mode == "fill":
        # Riempi il foglio: scala al massimo e ritaglia bordi eccedenti
        ratio = max(W_PIX / img.width, H_PIX / img.height)
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        # Ritaglio centratura se eccede
        left = max(0, (new_w - W_PIX) // 2)
        top = max(0, (new_h - H_PIX) // 2)
        img = img.crop((left, top, left + W_PIX, top + H_PIX))
    elif fit_mode == "original":
        # Dimensione 1:1 originale (eventualmente ritagliata se supera il foglio)
        if img.width > W_PIX or img.height > H_PIX:
            img = img.crop((0, 0, min(img.width, W_PIX), min(img.height, H_PIX)))
    else:
        # Default: "fit" (adatta mantenendo proporzioni con margini di sicurezza)
        img.thumbnail((avail_w, avail_h), Image.Resampling.LANCZOS)

    # 3. Canvas bianco Letter (5100 x 6600)
    canvas = Image.new("L", (W_PIX, H_PIX), 255)

    # 4. Posizionamento orizzontale e verticale
    ox = max(0, (W_PIX - img.width) // 2)
    if position == "top":
        oy = 150
    elif position == "bottom":
        oy = max(0, H_PIX - img.height - 150)
    else:
        # "center"
        oy = max(0, (H_PIX - img.height) // 2)

    canvas.paste(img, (ox, oy))
    return canvas


def convert_document_to_pclm_pages(doc_bytes, is_image=False, pages_opt="all", orientation="portrait", fit_mode="fit", position="center"):
    """
    Converte un documento (PDF o Immagine) in una lista di stream PCLm indipendenti (1 per pagina).
    Supporta selezione pagine, orientamento, scala e posizionamento.
    """
    pclm_pages = []

    if is_image or not doc_bytes.startswith(b"%PDF"):
        img = Image.open(io.BytesIO(doc_bytes))
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        img_gray = img.convert("L")
        canvas = render_image_on_canvas(img_gray, orientation=orientation, fit_mode=fit_mode, position=position)
        pclm_pages.append(build_exact_letter_pclm_canvas(canvas))
        return pclm_pages

    # Documento PDF
    doc = pymupdf.open(stream=doc_bytes, filetype="pdf")
    zoom = 600.0 / 72.0
    mat = pymupdf.Matrix(zoom, zoom)

    # Selezione pagine avanzata (es. "1", "2", "1-3", "1,3", "tutte")
    total_doc_pages = len(doc)
    selected_indices = []

    if pages_opt and pages_opt not in ["all", "tutte", "*"]:
        try:
            for part in str(pages_opt).replace(" ", "").split(","):
                if "-" in part:
                    bounds = part.split("-")
                    if len(bounds) == 2 and bounds[0].isdigit() and bounds[1].isdigit():
                        start_p = int(bounds[0])
                        end_p = int(bounds[1])
                        for p_num in range(start_p, end_p + 1):
                            idx = p_num - 1
                            if 0 <= idx < total_doc_pages and idx not in selected_indices:
                                selected_indices.append(idx)
                elif part.isdigit():
                    idx = int(part) - 1
                    if 0 <= idx < total_doc_pages and idx not in selected_indices:
                        selected_indices.append(idx)
        except Exception:
            selected_indices = list(range(total_doc_pages))

    if not selected_indices:
        selected_indices = list(range(total_doc_pages))

    for idx in selected_indices:
        page = doc[idx]
        pix = page.get_pixmap(matrix=mat, colorspace=pymupdf.csGRAY)
        page_img = Image.frombytes("L", [pix.width, pix.height], pix.samples)
        canvas = render_image_on_canvas(page_img, orientation=orientation, fit_mode=fit_mode, position=position)
        pclm_pages.append(build_exact_letter_pclm_canvas(canvas))

    return pclm_pages


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "service": "Windows 11 Compatible PCLm Converter for HP LaserJet P1102w",
            "status": "ready",
            "geometry": "Letter 5100x6600 (413 strips)",
            "options_supported": ["pages", "orientation", "fit", "position"]
        }).encode("utf-8"))

    def do_POST(self):
        auth_secret = self.headers.get("x-heartbeat-secret") or self.headers.get("X-Heartbeat-Secret")
        if not auth_secret and self.headers.get("Authorization"):
            auth_secret = self.headers.get("Authorization").replace("Bearer ", "").strip()

        if auth_secret != HEARTBEAT_SECRET:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Unauthorized: Invalid Secret"}).encode("utf-8"))
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Empty body"}).encode("utf-8"))
                return

            raw_body = self.rfile.read(content_length)
            content_type = self.headers.get("Content-Type", "").lower()

            is_img = "image/" in content_type
            doc_data = raw_body

            # Lettura parametri opzionali di stampa
            pages_opt = self.headers.get("x-print-pages", "all").strip().lower()
            orient_opt = self.headers.get("x-print-orientation", "portrait").strip().lower()
            fit_opt = self.headers.get("x-print-fit", "fit").strip().lower()
            pos_opt = self.headers.get("x-print-pos", "center").strip().lower()

            if "application/json" in content_type:
                payload = json.loads(raw_body.decode("utf-8", errors="ignore"))
                if "file_url" in payload:
                    f_url = str(payload["file_url"]).strip()
                    # Protezione SSRF: ammette ESCLUSIVAMENTE l'endpoint ufficiale Telegram Bot API su HTTPS
                    if not f_url.startswith("https://api.telegram.org/file/bot"):
                        self.send_response(400)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"error": "Security error: only official Telegram Bot API URLs are allowed"}).encode("utf-8"))
                        return

                    import urllib.request as u_req
                    req_dl = u_req.Request(f_url, headers={"User-Agent": "Mozilla/5.0"})
                    with u_req.urlopen(req_dl, timeout=30) as u_resp:
                        doc_data = u_resp.read()
                elif "data" in payload:
                    b64_str = payload.get("data", "")
                    doc_data = base64.b64decode(b64_str)

                filename = payload.get("filename", "").lower()
                if any(filename.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp"]):
                    is_img = True
                pages_opt = str(payload.get("pages", pages_opt)).lower()
                orient_opt = str(payload.get("orientation", orient_opt)).lower()
                fit_opt = str(payload.get("fit", fit_opt)).lower()
                pos_opt = str(payload.get("pos", pos_opt)).lower()

                # Action get_info per interrogare il totale pagine del documento
                if payload.get("action") == "get_info" or self.headers.get("x-get-info") == "1":
                    if is_img or not doc_data.startswith(b"%PDF"):
                        t_pages = 1
                    else:
                        d_info = pymupdf.open(stream=doc_data, filetype="pdf")
                        t_pages = len(d_info)
                    resp_payload = json.dumps({"total_pages": t_pages}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(resp_payload)))
                    self.end_headers()
                    self.wfile.write(resp_payload)
                    return

            # Action preview per generare l'anteprima visiva del foglio reale in formato JPEG
            is_preview = False
            if "application/json" in content_type and "payload" in locals():
                if payload.get("action") == "preview":
                    is_preview = True
            if self.headers.get("x-action") == "preview" or self.headers.get("X-Action") == "preview":
                is_preview = True

            if is_preview:
                if is_img or not doc_data.startswith(b"%PDF"):
                    img = Image.open(io.BytesIO(doc_data))
                    try:
                        img = ImageOps.exif_transpose(img)
                    except Exception:
                        pass
                    page_img = img.convert("L")
                    preview_p_num = 1
                    total_p_num = 1
                else:
                    d_info = pymupdf.open(stream=doc_data, filetype="pdf")
                    total_p_num = len(d_info)
                    zoom = 600.0 / 72.0
                    mat = pymupdf.Matrix(zoom, zoom)
                    # Determiniamo quale pagina mostrare nell'anteprima (la prima tra quelle selezionate)
                    sel_idx = 0
                    preview_page_req = payload.get("preview_page") or self.headers.get("x-preview-page")
                    if preview_page_req and str(preview_page_req).isdigit():
                        p_req = int(preview_page_req)
                        if 1 <= p_req <= total_p_num:
                            sel_idx = p_req - 1
                    elif pages_opt and pages_opt not in ["all", "tutte", "*"]:
                        first_digits = re.findall(r'\d+', pages_opt)
                        if first_digits:
                            try:
                                p_int = int(first_digits[0])
                                if 1 <= p_int <= total_p_num:
                                    sel_idx = p_int - 1
                            except Exception:
                                pass
                    page = d_info[sel_idx]
                    pix = page.get_pixmap(matrix=mat, colorspace=pymupdf.csGRAY)
                    page_img = Image.frombytes("L", [pix.width, pix.height], pix.samples)
                    preview_p_num = sel_idx + 1

                # Renderizza sul canvas Letter 5100x6600 a 600 DPI con orientamento, scala e posizionamento scelti
                canvas = render_image_on_canvas(
                    page_img,
                    orientation=orient_opt,
                    fit_mode=fit_opt,
                    position=pos_opt
                )

                # Bordo grigio elegante per delineare il perimetro del foglio reale su Telegram
                draw = ImageDraw.Draw(canvas)
                draw.rectangle([0, 0, W_PIX - 1, H_PIX - 1], outline=175, width=16)

                # Ridimensiona ad alta nitidezza (850x1100) per la visualizzazione ottimale su Telegram
                preview_res = canvas.resize((850, 1100), Image.Resampling.LANCZOS)
                out_buf = io.BytesIO()
                preview_res.convert("RGB").save(out_buf, format="JPEG", quality=85, optimize=True)
                preview_bytes = out_buf.getvalue()

                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(preview_bytes)))
                self.send_header("X-Preview-Page", str(preview_p_num))
                self.send_header("X-Total-Pages", str(total_p_num))
                self.end_headers()
                self.wfile.write(preview_bytes)
                return

            pclm_pages = convert_document_to_pclm_pages(
                doc_data,
                is_image=is_img,
                pages_opt=pages_opt,
                orientation=orient_opt,
                fit_mode=fit_opt,
                position=pos_opt
            )

            # Se documento a pagina singola, restituisce lo stream binario direttamente
            if len(pclm_pages) == 1:
                single_page = pclm_pages[0]
                self.send_response(200)
                self.send_header("Content-Type", "application/PCLm")
                self.send_header("Content-Length", str(len(single_page)))
                self.send_header("X-Total-Pages", "1")
                self.send_header("X-Page-Format", f"Letter-PCLm-600DPI-{orient_opt}")
                self.end_headers()
                self.wfile.write(single_page)
            else:
                # Se multi-pagina, restituisce un array JSON di pagine base64 per spooling sequenziale sicuro
                pages_b64 = [base64.b64encode(p).decode("ascii") for p in pclm_pages]
                resp_payload = json.dumps({
                    "total_pages": len(pages_b64),
                    "pages": pages_b64
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_payload)))
                self.send_header("X-Total-Pages", str(len(pages_b64)))
                self.end_headers()
                self.wfile.write(resp_payload)

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Conversion error: {str(e)}"}).encode("utf-8"))
