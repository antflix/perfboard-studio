"""Flat, labeled board illustrations for the exported build guide."""

from collections.abc import Callable

from PySide6.QtCore import QBuffer, QIODevice, QRectF
from PySide6.QtGui import QImage, QImageWriter, QPainter

from ..connectivity import FootprintLookup
from ..geometry import board_outline_mm
from ..guide import Guide, PartStep, document_at_step, step_focus, step_groups, step_groups_by_face
from ..model import BoardSide, PerfDocument
from .view2d import RULER_MARGIN_MM, BoardScene, HoleRulerItem


def render_guide_images(
    doc: PerfDocument,
    guide: Guide,
    lookup: FootprintLookup,
    progress: Callable[[int, int], bool] | None = None,
) -> dict[str, bytes]:
    """Draw one whole-board PNG per group and face, with the active items outlined."""
    groups = [group for phase in guide.phases for group in step_groups(phase, language=guide.language)]
    total = sum(len(step_groups_by_face(group)) for group in groups)
    outline = board_outline_mm(doc.board)
    margin = RULER_MARGIN_MM
    source = QRectF(outline.x, outline.y, outline.width, outline.height).adjusted(
        -margin, -margin, margin, margin
    )
    # Keep neighboring address labels readable at the image's native size.
    scale = 32 / doc.board.pitch
    width, height = round(source.width() * scale), round(source.height() * scale)
    target = QRectF(0, 0, width, height)
    images: dict[str, bytes] = {}
    done = 0
    for group in groups:
        done += len(group)
        current = document_at_step(doc, guide, done - 1)
        for bottom, work in step_groups_by_face(group):
            side: BoardSide = "bottom" if bottom else "top"
            scene = BoardScene(
                current, lookup, side=side, show_ratsnest=False,
                show_rulers=False, show_pin_names=False,
            )
            for item in scene.component_items.values():
                if item.comp.mirrored:
                    item.mount_side = "bottom"
            scene.addItem(HoleRulerItem(doc.board, side, label_px=16))
            scene.set_problem_focus(
                (),
                tuple(step_focus(step) for step in work if not isinstance(step, PartStep)),
                tuple(step_focus(step) for step in work if isinstance(step, PartStep)),
            )
            image = QImage(width, height, QImage.Format.Format_RGB32)
            painter = QPainter(image)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                scene.render(painter, target, source)
            finally:
                painter.end()
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            writer = QImageWriter(buffer, b"png")
            if not writer.write(image):
                raise RuntimeError(writer.errorString())
            images[step_focus(work[0])] = bytes(buffer.data().data())
            if progress is not None and not progress(len(images), total):
                return {}
    return images
