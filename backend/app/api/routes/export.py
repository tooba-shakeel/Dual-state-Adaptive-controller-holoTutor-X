"""
Export API Routes — exporting insights and chat history.
"""

from __future__ import annotations

import io
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.routes.auth import current_user
from app.api.routes.chat import owned_conversation
from app.core.models import InsightType
from app.dependencies import get_insights_service, get_conversation_store, get_document_service

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

try:
    from docx import Document
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

try:
    from openpyxl import Workbook
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/export", tags=["export"])

def _generate_pdf(content: str) -> io.BytesIO:
    if not HAS_REPORTLAB:
        raise HTTPException(status_code=500, detail="reportlab not installed")
        
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    Story = []
    
    for line in content.split('\n'):
        if line.strip():
            # escape xml characters
            clean_line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            p = Paragraph(clean_line, styles["Normal"])
            Story.append(p)
            Story.append(Spacer(1, 0.1 * 72))
            
    doc.build(Story)
    buffer.seek(0)
    return buffer

def _generate_word(content: str) -> io.BytesIO:
    if not HAS_DOCX:
        raise HTTPException(status_code=500, detail="python-docx not installed")
        
    doc = Document()
    for line in content.split('\n'):
        if line.strip():
            doc.add_paragraph(line)
    
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer

def _generate_excel(content: str) -> io.BytesIO:
    if not HAS_OPENPYXL:
        raise HTTPException(status_code=500, detail="openpyxl not installed")
        
    wb = Workbook()
    ws = wb.active
    for i, line in enumerate(content.split('\n'), start=1):
        if line.strip():
            ws.cell(row=i, column=1, value=line)
            
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer

@router.get("/insights/{document_id}/{format}")
async def export_insights(
    document_id: str,
    format: str,
    user: dict = Depends(current_user),
    insight_service = Depends(get_insights_service)
):
    """Export all generated insights for a document (own documents only)."""
    await get_document_service().get_document(document_id, user_id=user["id"])
    # We will generate/fetch all insights to compile a full report
    insight_types = list(InsightType)
    full_report = f"Insights Report for Document: {document_id}\n\n"
    
    for itype in insight_types:
        insight = await insight_service.get_or_generate_insight(document_id, itype)
        full_report += f"--- {itype.value.upper()} ---\n"
        full_report += f"{insight.content}\n\n"
        
    if format.lower() == "pdf":
        buffer = _generate_pdf(full_report)
        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=insights_{document_id}.pdf"}
        )
    elif format.lower() == "word":
        buffer = _generate_word(full_report)
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename=insights_{document_id}.docx"}
        )
    elif format.lower() == "excel":
        buffer = _generate_excel(full_report)
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=insights_{document_id}.xlsx"}
        )
    else:
        raise HTTPException(status_code=400, detail="Unsupported format. Use pdf, word, or excel.")

@router.get("/chat/{conversation_id}/txt")
async def export_chat(
    conversation_id: str,
    user: dict = Depends(current_user),
    store = Depends(get_conversation_store)
):
    """Export chat history as text (own conversations only)."""
    conv = await owned_conversation(conversation_id, user, store)

    content = f"Conversation: {conv.title}\n\n"
    for msg in conv.messages:
        content += f"[{msg.role.upper()}]: {msg.content}\n\n"
        
    buffer = io.BytesIO(content.encode('utf-8'))
    return StreamingResponse(
        buffer,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename=chat_{conversation_id}.txt"}
    )
