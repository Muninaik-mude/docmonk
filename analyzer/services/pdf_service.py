import logging

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


def extract_text_with_positions(pdf_bytes: bytes) -> list[dict]:
    """
    Extract text blocks with their page number and bounding box.
    Returns list of: {page_num, text, bbox: (x0, y0, x1, y1)}
    """
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    text_blocks = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        blocks = page.get_text("blocks")  # (x0, y0, x1, y1, text, block_no, block_type)
        for block in blocks:
            if block[6] == 0:  # text block (not image)
                text_blocks.append({
                    "page_num": page_num,
                    "text": block[4].strip(),
                    "bbox": (block[0], block[1], block[2], block[3]),
                })

    doc.close()
    return text_blocks


def get_full_text(text_blocks: list[dict]) -> str:
    """Concatenate all text blocks into a single string."""
    return "\n".join(block["text"] for block in text_blocks)


def annotate_pdf(pdf_bytes: bytes, annotations: list[dict]) -> bytes:
    """
    Apply color annotations to PDF using PyMuPDF.

    Each annotation dict:
    {
        "page_num": int,
        "bbox": (x0, y0, x1, y1),
        "highlight_color": tuple (R, G, B) normalized 0-1,
        "inserted_text": str or None,
        "inserted_text_color": tuple (R, G, B) or None
    }
    """
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')

    for annotation in annotations:
        page_num = annotation["page_num"]
        if page_num >= len(doc):
            logger.warning("Page %d out of range, skipping annotation", page_num)
            continue

        page = doc[page_num]
        bbox = fitz.Rect(annotation["bbox"])
        highlight_color = annotation.get("highlight_color")

        # Draw highlight rectangle
        if highlight_color:
            shape = page.new_shape()
            shape.draw_rect(bbox)
            shape.finish(
                color=highlight_color,
                fill=highlight_color,
                fill_opacity=0.3,
            )
            shape.commit()

        # Insert text below the highlighted area
        inserted_text = annotation.get("inserted_text")
        if inserted_text:
            insertion_color = annotation.get("inserted_text_color", highlight_color)
            text_y_start = bbox.y1 + 5
            page_width = page.rect.width

            # Calculate text box dimensions
            text_rect = fitz.Rect(
                bbox.x0,
                text_y_start,
                page_width - 30,
                text_y_start + 80,
            )

            # Check if text box fits on current page
            if text_rect.y1 > page.rect.height - 20:
                text_rect = fitz.Rect(
                    30,
                    text_y_start,
                    page_width - 30,
                    page.rect.height - 20,
                )

            # Draw background for inserted text
            if insertion_color:
                shape = page.new_shape()
                shape.draw_rect(text_rect)
                shape.finish(
                    color=insertion_color,
                    fill=insertion_color,
                    fill_opacity=0.25,
                )
                shape.commit()

            # Insert the text
            page.insert_textbox(
                text_rect,
                inserted_text,
                fontsize=8,
                fontname="helv",
                color=(0, 0, 0),
                align=fitz.TEXT_ALIGN_LEFT,
            )

    result_bytes = doc.tobytes()
    doc.close()
    return result_bytes
