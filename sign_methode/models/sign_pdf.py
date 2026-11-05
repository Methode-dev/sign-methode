# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""PDF plumbing of the signature engine, free of ORM code.

Coordinates everywhere are fractions of the page's crop box with the origin at
the top-left corner, as the browser shows the page: ``x``, ``y``, ``w`` and
``h`` all lie in [0, 1]. Documents are normalised once (rotation folded into
the content) so the viewer and the stamper agree on what "top-left" means.
"""

import asyncio
import base64
import binascii
import hashlib
import io
import logging

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_logger = logging.getLogger(__name__)

try:
    from asn1crypto import keys as asn1_keys, pem as asn1_pem, x509 as asn1_x509
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        pkcs12,
    )
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.pdf_utils.reader import PdfFileReader as HankoReader
    from pyhanko.sign import fields as sig_fields, signers, timestamps
    from pyhanko.sign.validation import (
        validate_pdf_signature,
        validate_pdf_timestamp,
    )
    from pyhanko_certvalidator import ValidationContext
    from pyhanko_certvalidator.registry import SimpleCertificateStore
except ImportError:  # pragma: no cover - checked by external_dependencies
    signers = None

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_IMAGE_BYTES = 512 * 1024
INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#5F6674")
BRAND = colors.HexColor("#0E58F0")
LINE = colors.HexColor("#D5DCEB")


class SignPdfError(Exception):
    """A document or a value the engine refuses to work with."""


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
def normalize_pdf(pdf_bytes):
    """Return the document with page rotation folded into the content.

    Refuses anything that is not a readable, unencrypted PDF.
    """
    if not pdf_bytes or not pdf_bytes.lstrip()[:5] == b"%PDF-":
        raise SignPdfError("not a PDF")
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            raise SignPdfError("encrypted PDF")
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        for page in writer.pages:
            if page.get("/Rotate"):
                page.transfer_rotation_to_content()
        out = io.BytesIO()
        writer.write(out)
    except SignPdfError:
        raise
    except Exception as e:
        raise SignPdfError(f"unreadable PDF: {e}") from e
    return out.getvalue()


def page_count(pdf_bytes):
    return len(PdfReader(io.BytesIO(pdf_bytes)).pages)


def _box(page):
    cb = page.cropbox
    left, bottom = float(cb.left), float(cb.bottom)
    return left, bottom, float(cb.width), float(cb.height)


def _rect(page, item):
    """Item fractions -> (llx, lly, width, height) in PDF points."""
    left, bottom, width, height = _box(page)
    w = item["w"] * width
    h = item["h"] * height
    return left + item["x"] * width, bottom + (1 - item["y"]) * height - h, w, h


def decode_png(value):
    """Validate a ``data:image/png;base64,...`` value and return its bytes."""
    prefix = "data:image/png;base64,"
    if not isinstance(value, str) or not value.startswith(prefix):
        raise SignPdfError("signature must be a PNG data URL")
    if len(value) > MAX_IMAGE_BYTES * 4 // 3 + len(prefix) + 4:
        raise SignPdfError("signature image too large")
    try:
        data = base64.b64decode(value[len(prefix) :], validate=True)
    except (binascii.Error, ValueError) as e:
        raise SignPdfError("signature is not valid base64") from e
    if not data.startswith(PNG_MAGIC) or len(data) > MAX_IMAGE_BYTES:
        raise SignPdfError("signature is not a PNG")
    return data


def _fit_font(c, text, font, width, height):
    size = max(min(height * 0.62, 14), 5)
    while size > 5 and c.stringWidth(text, font, size) > width * 0.96:
        size -= 0.5
    return size


def _draw_item(c, page, item):
    x, y, w, h = _rect(page, item)
    value = item.get("value")
    ftype = item["type"]
    if ftype in ("signature", "initials"):
        if not value:
            return
        img = ImageReader(io.BytesIO(decode_png(value)))
        c.drawImage(
            img, x, y, w, h, mask="auto", preserveAspectRatio=True, anchor="c"
        )
    elif ftype == "checkbox":
        size = min(w, h)
        c.setStrokeColor(INK)
        c.setLineWidth(max(size * 0.06, 0.6))
        c.rect(x, y, size, size)
        if value:
            c.line(x + size * 0.2, y + size * 0.5, x + size * 0.42, y + size * 0.22)
            c.line(x + size * 0.42, y + size * 0.22, x + size * 0.82, y + size * 0.8)
    else:
        text = str(value or "")
        if not text:
            return
        font = "Helvetica"
        size = _fit_font(c, text, font, w, h)
        while text and c.stringWidth(text, font, size) > w:
            text = text[:-1]
        c.setFillColor(INK)
        c.setFont(font, size)
        c.drawString(x + 1, y + (h - size) / 2 + size * 0.2, text)


def stamp(pdf_bytes, items):
    """Draw item values (``{type, page, x, y, w, h, value}``) onto the pages."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter(clone_from=reader)
    by_page = {}
    for item in items:
        if not 0 <= item["page"] < len(writer.pages):
            raise SignPdfError(f"item on missing page {item['page']}")
        by_page.setdefault(item["page"], []).append(item)
    for index, page_items in by_page.items():
        page = writer.pages[index]
        mb = page.mediabox
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(float(mb.right), float(mb.top)))
        for item in page_items:
            _draw_item(c, page, item)
        c.save()
        page.merge_page(PdfReader(io.BytesIO(buf.getvalue())).pages[0])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def append_pdf(pdf_bytes, extra_bytes):
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes)))
    writer.append(PdfReader(io.BytesIO(extra_bytes)))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Signature page ("simple" mode): one block per signer, appended at the end
# ---------------------------------------------------------------------------
BLOCKS_PER_PAGE = 4


def signature_pages(title, subtitle, signers_info, labels):
    """Render the signature page(s) and the placement of each signer's fields.

    ``signers_info`` is a list of ``{name, email, role}``; ``labels`` holds the
    translated strings (``signature``, ``date``, ``footer``). Returns
    ``(pdf_bytes, items)`` where each item carries ``signer_index`` and page
    numbers relative to the returned document.
    """
    width, height = A4
    margin = 18 * mm
    block_h = 42 * mm
    gap = 8 * mm
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    items = []
    chunks = [
        signers_info[i : i + BLOCKS_PER_PAGE]
        for i in range(0, len(signers_info), BLOCKS_PER_PAGE)
    ] or [[]]
    for page_index, chunk in enumerate(chunks):
        c.setFillColor(BRAND)
        c.rect(margin, height - margin - 2, 28 * mm, 2, stroke=0, fill=1)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(margin, height - margin - 26, title)
        c.setFont("Helvetica", 9.5)
        c.setFillColor(MUTED)
        c.drawString(margin, height - margin - 42, subtitle[:120])
        top = height - margin - 70
        for pos, info in enumerate(chunk):
            signer_index = page_index * BLOCKS_PER_PAGE + pos
            y_top = top - pos * (block_h + gap)
            y = y_top - block_h
            c.setStrokeColor(LINE)
            c.setLineWidth(0.8)
            c.roundRect(margin, y, width - 2 * margin, block_h, 8, stroke=1, fill=0)
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 11.5)
            c.drawString(margin + 12, y_top - 20, info["name"][:60])
            c.setFont("Helvetica", 9)
            c.setFillColor(MUTED)
            c.drawString(margin + 12, y_top - 34, (info.get("email") or "")[:70])
            if info.get("role"):
                c.drawString(margin + 12, y_top - 47, info["role"][:70])
            c.drawString(margin + 12, y + 12, labels["date"])
            sig_w, sig_h = 70 * mm, block_h - 14 * mm
            sig_x = width - margin - 12 - sig_w
            sig_y = y + 10 * mm
            c.setStrokeColor(LINE)
            c.setDash(3, 3)
            c.line(sig_x, sig_y, sig_x + sig_w, sig_y)
            c.setDash()
            c.setFont("Helvetica", 7.5)
            c.drawString(sig_x, sig_y - 10, labels["signature"])

            def frac(px, py, pw, ph):
                return {
                    "x": px / width,
                    "y": 1 - (py + ph) / height,
                    "w": pw / width,
                    "h": ph / height,
                }

            items.append(
                dict(
                    frac(sig_x, sig_y + 2, sig_w, sig_h - 2),
                    type="signature",
                    page=page_index,
                    signer_index=signer_index,
                    required=True,
                )
            )
            label_w = c.stringWidth(labels["date"], "Helvetica", 9) + 4
            items.append(
                dict(
                    frac(margin + 12 + label_w, y + 7, 60 * mm, 5 * mm),
                    type="date",
                    page=page_index,
                    signer_index=signer_index,
                    required=True,
                )
            )
        c.setFont("Helvetica", 7.5)
        c.setFillColor(MUTED)
        c.drawString(margin, margin / 2, labels["footer"][:150])
        c.showPage()
    c.save()
    return buf.getvalue(), items


# ---------------------------------------------------------------------------
# Evidence ("certificate of completion") pages
# ---------------------------------------------------------------------------
def evidence_pages(data):
    """Render the evidence summary appended to the signed document.

    ``data`` = ``{title, intro, sections: [{title, rows: [[label, value]]}],
    events: {title, header: [...], rows: [[...]]}, footer}``, all translated.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=data["title"],
    )
    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=16, leading=20)
    h2 = ParagraphStyle(
        "h2", fontName="Helvetica-Bold", fontSize=11, leading=14, spaceBefore=10
    )
    body = ParagraphStyle(
        "b", fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK
    )
    small = ParagraphStyle(
        "s", fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=MUTED
    )
    mono = ParagraphStyle("m", parent=small, fontName="Courier", textColor=INK)

    def p(text, style=body):
        text = (
            str(text if text is not None else "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return Paragraph(text, style)

    story = [p(data["title"], h1), Spacer(1, 4), p(data["intro"], small)]
    grid = TableStyle(
        [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
    )
    for section in data["sections"]:
        story.append(p(section["title"], h2))
        rows = [
            [p(label, small), p(value, mono if section.get("mono") else body)]
            for label, value in section["rows"]
        ]
        story.append(Table(rows, colWidths=[48 * mm, None], style=grid))
    events = data["events"]
    story.append(p(events["title"], h2))
    rows = [[p(h, small) for h in events["header"]]] + [
        [p(v) for v in row] for row in events["rows"]
    ]
    table = Table(
        rows, colWidths=[34 * mm, 30 * mm, 46 * mm, None], repeatRows=1, style=grid
    )
    story.append(table)
    story += [Spacer(1, 8), p(data["footer"], small)]
    doc.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Cryptography: seal certificate, RFC 3161 timestamps, PAdES
# ---------------------------------------------------------------------------
def load_seal(p12_bytes, password):
    """Load a PKCS#12 seal into a pyHanko signer (key, cert and chain)."""
    try:
        key, cert, chain = pkcs12.load_key_and_certificates(
            p12_bytes, (password or "").encode() or None
        )
    except Exception as e:
        raise SignPdfError(f"cannot read the seal certificate: {e}") from e
    if not key or not cert:
        raise SignPdfError("the PKCS#12 file holds no key or certificate")
    der_key = key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
    store = SimpleCertificateStore.from_certs(
        [asn1_x509.Certificate.load(c.public_bytes(Encoding.DER)) for c in chain]
    )
    return signers.SimpleSigner(
        signing_cert=asn1_x509.Certificate.load(cert.public_bytes(Encoding.DER)),
        signing_key=asn1_keys.PrivateKeyInfo.load(der_key),
        cert_registry=store,
    )


def seal_certificate_info(p12_bytes, password):
    signer = load_seal(p12_bytes, password)
    cert = signer.signing_cert
    return {
        "subject": cert.subject.human_friendly,
        "issuer": cert.issuer.human_friendly,
        "not_after": cert["tbs_certificate"]["validity"]["not_after"].native,
        "serial": format(cert.serial_number, "X"),
    }


def http_timestamper(url, username=None, password=None):
    auth = (username, password) if username else None
    return timestamps.HTTPTimeStamper(url, auth=auth, timeout=20)


def timestamp_digest(timestamper, digest_bytes):
    """Ask the TSA to timestamp a SHA-256 digest.

    Returns ``(token_der, gen_time, serial)``.
    """
    token = asyncio.run(timestamper.async_timestamp(digest_bytes, "sha256"))
    tst_info = token["content"]["encap_content_info"]["content"].parsed
    return (
        token.dump(),
        tst_info["gen_time"].native,
        format(tst_info["serial_number"].native, "X"),
    )


def validation_context(signer, extra_roots=(), fetch=True):
    """Trust the system store, plus the root of the seal's own chain and any
    extra roots (e.g. the TSA's, or an EU trusted-list CA missing from it)."""
    roots = [
        c
        for c in list(signer.cert_registry) + list(extra_roots)
        if c.self_signed in ("maybe", "yes")
    ]
    return ValidationContext(
        extra_trust_roots=roots,
        other_certs=list(signer.cert_registry),
        allow_fetching=fetch,
        revocation_mode="soft-fail",
    )


def load_pem_certificates(pem_text):
    """Every certificate of a PEM bundle."""
    data = (pem_text or "").encode()
    if not data.strip():
        return []
    try:
        return [
            asn1_x509.Certificate.load(der)
            for _type, _headers, der in asn1_pem.unarmor(data, multiple=True)
        ]
    except Exception as e:
        raise SignPdfError(f"invalid PEM certificate: {e}") from e


def pades_seal(
    pdf_bytes,
    signer,
    signatures,
    timestamper=None,
    ltv=True,
    vc=None,
):
    """Apply one PAdES signature per entry of ``signatures`` with the seal.

    Each entry is ``{field, name, reason, location}``. With a timestamper the
    signatures are B-T; with ``ltv`` validation data is embedded (B-LT) and a
    final document timestamp makes the file B-LTA. Returns
    ``(pdf_bytes, level)``.
    """
    data = pdf_bytes
    level = "B-B"
    for index, entry in enumerate(signatures):
        meta = signers.PdfSignatureMetadata(
            field_name=entry["field"],
            name=entry["name"],
            reason=entry.get("reason"),
            location=entry.get("location"),
            md_algorithm="sha256",
            subfilter=sig_fields.SigSeedSubFilter.PADES,
            embed_validation_info=bool(ltv and vc),
            validation_context=vc if ltv else None,
            certify=index == 0,
            docmdp_permissions=sig_fields.MDPPerm.FILL_FORMS,
        )
        writer = IncrementalPdfFileWriter(io.BytesIO(data), strict=False)
        out = signers.PdfSigner(
            meta, signer=signer, timestamper=timestamper
        ).sign_pdf(writer)
        data = out.getvalue()
    if timestamper:
        level = "B-T"
        if ltv and vc:
            writer = IncrementalPdfFileWriter(io.BytesIO(data), strict=False)
            out = signers.PdfTimeStamper(timestamper).timestamp_pdf(
                writer, "sha256", validation_context=vc, in_place=False
            )
            data = out.getvalue()
            level = "B-LTA"
    return data, level


def validate_signed_pdf(pdf_bytes, vc):
    """Validation summary of every signature in a document (used by tests)."""
    reader = HankoReader(io.BytesIO(pdf_bytes), strict=False)
    results = []
    for sig in reader.embedded_signatures:
        if sig.sig_object.get("/Type") == "/DocTimeStamp":
            status = validate_pdf_timestamp(sig, validation_context=vc)
            kind = "timestamp"
        else:
            status = validate_pdf_signature(sig, vc)
            kind = "signature"
        results.append(
            {
                "kind": kind,
                "field": sig.field_name,
                "intact": status.intact,
                "valid": status.valid,
                "trusted": status.trusted,
                "coverage": str(getattr(status, "coverage", "")),
                "timestamped": bool(getattr(status, "timestamp_validity", None)),
            }
        )
    return results
