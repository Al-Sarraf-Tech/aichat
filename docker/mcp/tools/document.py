"""
Document tool handlers — OCR, PDF, and document ingestion.

Resolved actions: ocr_image, ocr_pdf, docs_ingest, docs_extract_tables,
                  pdf_read, pdf_edit, pdf_fill_form, pdf_merge, pdf_split
(dispatched from the 'document' mega-tool via _resolve_mega_tool)
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

from tools import TOOL_HANDLERS  # type: ignore[import]
from tools._helpers import (  # type: ignore[import]
    text, get_client, resolve_image_path,
    OCR_URL, DOCS_URL, PDF_URL, BROWSER_WORKSPACE,
)


# ── OCR ──────────────────────────────────────────────────────────

async def _ocr_image(args: dict[str, Any]) -> list[dict[str, Any]]:
    path = str(args.get("path", "")).strip()
    lang = str(args.get("lang", "eng")).strip() or "eng"
    if not path:
        return text("ocr_image: 'path' is required")
    local = resolve_image_path(path)
    if not local:
        return text(f"ocr_image: file not found — {path}")
    with open(local, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode("ascii")
    try:
        async with get_client() as c:
            r = await c.post(OCR_URL, json={"b64": b64, "lang": lang}, timeout=60)
            r.raise_for_status()
            d = r.json()
    except Exception as exc:
        return text(f"ocr_image: OCR service failed — {exc}")
    return text(f"OCR result ({d.get('word_count', 0)} words):\n\n{d.get('text', '')}")


async def _ocr_pdf(args: dict[str, Any]) -> list[dict[str, Any]]:
    path  = str(args.get("path", "")).strip()
    lang  = str(args.get("lang", "eng")).strip() or "eng"
    pages = list(args.get("pages", []))
    if not path:
        return text("ocr_pdf: 'path' is required")
    local = resolve_image_path(path)
    if not local:
        return text(f"ocr_pdf: file not found — {path}")
    with open(local, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode("ascii")
    try:
        async with get_client() as c:
            r = await c.post(
                f"{OCR_URL}/pdf",
                json={"b64_pdf": b64, "lang": lang, "pages": pages or None},
                timeout=120,
            )
            r.raise_for_status()
            d = r.json()
    except Exception as exc:
        return text(f"ocr_pdf: OCR service failed — {exc}")
    return text(
        f"PDF OCR: {d.get('page_count', 0)} pages, {d.get('word_count', 0)} words"
        f"\n\n{d.get('full_text', '')}"
    )


# ── Document Ingestion ───────────────────────────────────────────

async def _docs_handler(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Handles both docs_ingest and docs_extract_tables."""
    action = args.get("_action", "docs_ingest")
    url_arg  = str(args.get("url",  "")).strip()
    path_arg = str(args.get("path", "")).strip()
    filename = str(args.get("filename", "")).strip()

    async with get_client() as c:
        if url_arg:
            try:
                r = await c.post(f"{DOCS_URL}/ingest/url", json={"url": url_arg}, timeout=60)
                r.raise_for_status()
                d = r.json()
            except Exception as exc:
                return text(f"{action}: docs service failed — {exc}")
        elif path_arg:
            local = resolve_image_path(path_arg)
            if not local:
                return text(f"{action}: file not found — {path_arg}")
            if not filename:
                filename = os.path.basename(local)
            b64 = base64.standard_b64encode(open(local, "rb").read()).decode("ascii")
            try:
                endpoint = "/ingest" if action == "docs_ingest" else "/tables"
                r = await c.post(
                    f"{DOCS_URL}{endpoint}",
                    json={"b64": b64, "filename": filename},
                    timeout=60,
                )
                r.raise_for_status()
                d = r.json()
            except Exception as exc:
                return text(f"{action}: docs service failed — {exc}")
        else:
            return text(f"{action}: 'url' or 'path' is required")

    if action == "docs_ingest":
        md    = d.get("markdown", "")
        title = d.get("title", "")
        words = d.get("word_count", 0)
        tables = d.get("tables_found", 0)
        return text(f"# {title}\n\n_{words} words, {tables} tables_\n\n{md}")
    else:
        tables = d.get("tables", [])
        if not tables:
            return text("No tables found in document.")
        return text(json.dumps(tables, indent=2))


async def _docs_ingest(args: dict[str, Any]) -> list[dict[str, Any]]:
    args["_action"] = "docs_ingest"
    return await _docs_handler(args)


async def _docs_extract_tables(args: dict[str, Any]) -> list[dict[str, Any]]:
    args["_action"] = "docs_extract_tables"
    return await _docs_handler(args)


# ── PDF Operations ───────────────────────────────────────────────

def _workspace_file_for_pdf(raw_path: str) -> tuple[str | None, str | None]:
    """Resolve a path to a local file + workspace-relative path for PDF ops."""
    rp = str(raw_path or "").strip()
    if not rp:
        return None, None

    rel = ""
    if rp.startswith("/workspace/"):
        rel = rp[len("/workspace/"):].lstrip("/")
    elif rp.startswith("/docker/human_browser/workspace/"):
        rel = rp[len("/docker/human_browser/workspace/"):].lstrip("/")
    elif rp.startswith(BROWSER_WORKSPACE + "/"):
        rel = rp[len(BROWSER_WORKSPACE) + 1:].lstrip("/")
    elif os.path.isabs(rp):
        try:
            rel_guess = os.path.relpath(rp, BROWSER_WORKSPACE)
        except Exception:
            return None, None
        if rel_guess.startswith(".."):
            return None, None
        rel = rel_guess.replace("\\", "/").lstrip("/")
    else:
        rel = rp.lstrip("/")

    local = os.path.normpath(os.path.join(BROWSER_WORKSPACE, rel))
    ws_root = os.path.normpath(BROWSER_WORKSPACE)
    if not (local == ws_root or local.startswith(ws_root + os.sep)):
        return None, None
    if os.path.isfile(local):
        return local, f"/workspace/{rel}".replace("//", "/")

    base = os.path.basename(rel)
    if not base:
        return None, None
    alt_local = os.path.join(BROWSER_WORKSPACE, base)
    if os.path.isfile(alt_local):
        return alt_local, f"/workspace/{base}"
    return None, None


def _workspace_target_for_pdf(raw_path: str) -> str:
    """Resolve a target output path for PDF ops."""
    rp = str(raw_path or "").strip()
    if not rp:
        return ""
    if rp.startswith("/workspace/"):
        return rp
    if rp.startswith("/docker/human_browser/workspace/"):
        return f"/workspace/{rp[len('/docker/human_browser/workspace/'):]}"
    if rp.startswith(BROWSER_WORKSPACE + "/"):
        return f"/workspace/{rp[len(BROWSER_WORKSPACE) + 1:]}"
    if os.path.isabs(rp):
        try:
            rel = os.path.relpath(rp, BROWSER_WORKSPACE)
        except Exception:
            return ""
        if rel.startswith(".."):
            return ""
        return f"/workspace/{rel}".replace("//", "/")
    return f"/workspace/{rp.lstrip('/')}"


async def _pdf_handler(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Unified handler for pdf_read, pdf_edit, pdf_fill_form, pdf_merge, pdf_split."""
    action = str(args.get("_pdf_action", "pdf_read"))

    async with get_client(timeout=120) as c:
        if action == "pdf_read":
            path = str(args.get("path", "")).strip()
            if not path:
                return text("pdf_read: 'path' is required")
            local, ws_path = _workspace_file_for_pdf(path)
            if not local or not ws_path:
                return text(f"pdf_read: file not found — {path}")
            b64 = base64.standard_b64encode(open(local, "rb").read()).decode("ascii")
            pages = args.get("pages")
            body: dict[str, Any] = {"b64_pdf": b64}
            if pages:
                body["pages"] = pages
            try:
                r = await c.post(f"{PDF_URL}/read", json=body)
                r.raise_for_status()
                d = r.json()
            except Exception as exc:
                return text(f"pdf_read: failed — {exc}")
            return text(
                f"PDF: {d.get('page_count', '?')} pages\n\n{d.get('text', '')}"
            )

        if action == "pdf_edit":
            path = str(args.get("path", "")).strip()
            if not path:
                return text("pdf_edit: 'path' is required")
            local, ws_path = _workspace_file_for_pdf(path)
            if not local or not ws_path:
                return text(f"pdf_edit: file not found — {path}")
            edits = args.get("edits", [])
            if not edits:
                return text("pdf_edit: 'edits' array is required")
            b64 = base64.standard_b64encode(open(local, "rb").read()).decode("ascii")
            output_name = str(args.get("output", "")).strip()
            if not output_name:
                output_name = os.path.splitext(os.path.basename(local))[0] + "_edited.pdf"
            try:
                r = await c.post(f"{PDF_URL}/edit", json={
                    "b64_pdf": b64, "edits": edits, "output": output_name,
                })
                r.raise_for_status()
                d = r.json()
            except Exception as exc:
                return text(f"pdf_edit: failed — {exc}")
            if d.get("b64_pdf"):
                out_path = os.path.join(BROWSER_WORKSPACE, output_name)
                with open(out_path, "wb") as f:
                    f.write(base64.standard_b64decode(d["b64_pdf"]))
                return text(f"PDF edited → /workspace/{output_name}")
            return text(f"pdf_edit: {d.get('error', 'unknown error')}")

        if action == "pdf_fill_form":
            path = str(args.get("path", "")).strip()
            if not path:
                return text("pdf_fill_form: 'path' is required")
            local, ws_path = _workspace_file_for_pdf(path)
            if not local or not ws_path:
                return text(f"pdf_fill_form: file not found — {path}")
            fields = args.get("fields", {})
            if not fields:
                return text("pdf_fill_form: 'fields' is required")
            b64 = base64.standard_b64encode(open(local, "rb").read()).decode("ascii")
            output_name = str(args.get("output", "")).strip()
            if not output_name:
                output_name = os.path.splitext(os.path.basename(local))[0] + "_filled.pdf"
            try:
                r = await c.post(f"{PDF_URL}/fill", json={
                    "b64_pdf": b64, "fields": fields, "output": output_name,
                })
                r.raise_for_status()
                d = r.json()
            except Exception as exc:
                return text(f"pdf_fill_form: failed — {exc}")
            if d.get("b64_pdf"):
                out_path = os.path.join(BROWSER_WORKSPACE, output_name)
                with open(out_path, "wb") as f:
                    f.write(base64.standard_b64decode(d["b64_pdf"]))
                return text(f"PDF form filled → /workspace/{output_name}")
            return text(f"pdf_fill_form: {d.get('error', 'unknown error')}")

        if action == "pdf_merge":
            paths = list(args.get("paths", []))
            if len(paths) < 2:
                return text("pdf_merge: at least 2 'paths' are required")
            b64_list = []
            for p in paths:
                local, _ = _workspace_file_for_pdf(str(p))
                if not local:
                    return text(f"pdf_merge: file not found — {p}")
                b64_list.append(
                    base64.standard_b64encode(open(local, "rb").read()).decode("ascii")
                )
            output_name = str(args.get("output", "merged.pdf")).strip()
            try:
                r = await c.post(f"{PDF_URL}/merge", json={
                    "pdfs": b64_list, "output": output_name,
                })
                r.raise_for_status()
                d = r.json()
            except Exception as exc:
                return text(f"pdf_merge: failed — {exc}")
            if d.get("b64_pdf"):
                out_path = os.path.join(BROWSER_WORKSPACE, output_name)
                with open(out_path, "wb") as f:
                    f.write(base64.standard_b64decode(d["b64_pdf"]))
                return text(f"PDFs merged → /workspace/{output_name}")
            return text(f"pdf_merge: {d.get('error', 'unknown error')}")

        if action == "pdf_split":
            path = str(args.get("path", "")).strip()
            if not path:
                return text("pdf_split: 'path' is required")
            local, _ = _workspace_file_for_pdf(path)
            if not local:
                return text(f"pdf_split: file not found — {path}")
            pages = args.get("pages", [])
            if not pages:
                return text("pdf_split: 'pages' list is required")
            b64 = base64.standard_b64encode(open(local, "rb").read()).decode("ascii")
            try:
                r = await c.post(f"{PDF_URL}/split", json={
                    "b64_pdf": b64, "pages": pages,
                })
                r.raise_for_status()
                d = r.json()
            except Exception as exc:
                return text(f"pdf_split: failed — {exc}")
            output_files = d.get("files", [])
            if output_files:
                saved = []
                for fi in output_files:
                    fn = fi.get("filename", f"split_{len(saved)}.pdf")
                    out_path = os.path.join(BROWSER_WORKSPACE, fn)
                    with open(out_path, "wb") as f:
                        f.write(base64.standard_b64decode(fi["b64_pdf"]))
                    saved.append(f"/workspace/{fn}")
                return text(f"PDF split into {len(saved)} file(s):\n" + "\n".join(saved))
            return text(f"pdf_split: {d.get('error', 'unknown error')}")

    return text(f"pdf: unknown action '{action}'")


# Wrapper handlers for mega-tool dispatch
async def _pdf_read(args: dict[str, Any]) -> list[dict[str, Any]]:
    args["_pdf_action"] = "pdf_read"
    return await _pdf_handler(args)

async def _pdf_edit(args: dict[str, Any]) -> list[dict[str, Any]]:
    args["_pdf_action"] = "pdf_edit"
    return await _pdf_handler(args)

async def _pdf_fill_form(args: dict[str, Any]) -> list[dict[str, Any]]:
    args["_pdf_action"] = "pdf_fill_form"
    return await _pdf_handler(args)

async def _pdf_merge(args: dict[str, Any]) -> list[dict[str, Any]]:
    args["_pdf_action"] = "pdf_merge"
    return await _pdf_handler(args)

async def _pdf_split(args: dict[str, Any]) -> list[dict[str, Any]]:
    args["_pdf_action"] = "pdf_split"
    return await _pdf_handler(args)


# Register all handlers
TOOL_HANDLERS["ocr_image"]          = _ocr_image
TOOL_HANDLERS["ocr_pdf"]            = _ocr_pdf
TOOL_HANDLERS["docs_ingest"]        = _docs_ingest
TOOL_HANDLERS["docs_extract_tables"] = _docs_extract_tables
TOOL_HANDLERS["pdf_read"]           = _pdf_read
TOOL_HANDLERS["pdf_edit"]           = _pdf_edit
TOOL_HANDLERS["pdf_fill_form"]      = _pdf_fill_form
TOOL_HANDLERS["pdf_merge"]          = _pdf_merge
TOOL_HANDLERS["pdf_split"]          = _pdf_split
