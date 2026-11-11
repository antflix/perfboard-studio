"""Flat, labeled board illustrations for the exported build guide."""

from collections.abc import Callable
from dataclasses import replace
from itertools import product

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QImageWriter, QPainter, QPen

from ..connectivity import FootprintLookup
from ..geometry import board_outline_mm, format_hole
from ..guide import (
    Guide,
    GuideStep,
    PartStep,
    all_steps,
    assembly_phases,
    document_at_step,
    step_focus,
)
from ..model import Board, BoardSide, HoleCoord, PerfDocument
from ..phrasebook import Phrasebook, phrasebook
from .view2d import RULER_MARGIN_MM, BoardScene, HoleRulerItem, hole_to_screen


def _annotate(
    painter: QPainter, doc: PerfDocument, side: BoardSide, work: tuple[GuideStep, ...],
    source: QRectF, target: QRectF, say: Phrasebook,
) -> None:
    """Place address tags in free space around their pads, with a line back to each pad."""
    board = doc.board
    active_ids = {step_focus(step) for step in work}
    tags: dict[HoleCoord, tuple[str, bool]] = {}
    for wire in doc.conductors:
        if wire.id not in active_ids:
            for hole in (wire.path[0], wire.path[-1]):
                tags[hole] = format_hole(hole, board), False
    for step in work:
        if isinstance(step, PartStep):
            for pin, hole in step.pin_holes:
                if pin == "1":
                    tags[hole] = say("Pin 1 · {hole}", hole=format_hole(hole, board)), True
        else:
            for hole in (step.path[0], step.path[-1]):
                tags[hole] = format_hole(hole, board), True
            if step.cut is not None:
                for i, hole in enumerate(step.path[1:-1], 1):
                    tags[hole] = say("Bend {number} · {hole}", number=i, hole=format_hole(hole, board)), True

    font = QFont()
    font.setPixelSize(16)
    font.setBold(True)
    painter.setFont(font)
    metrics = QFontMetricsF(font)
    a1 = _image_point(HoleCoord(0, 0), board, side, source, target)
    field = target.adjusted(77, a1.y() - 4, -5, -5)
    placed = [QRectF(a1.x() - 10, a1.y() - 10, 20, 20)]
    # Nearest vacant position first, with room to move around crowded endpoints.
    offsets = sorted(product((0, -1, 1, -2, 2, -3, 3, -4, 4), repeat=2),
                     key=lambda offset: abs(offset[0]) + abs(offset[1]))
    for hole, (text, active) in sorted(tags.items(), key=lambda item: not item[1][1]):
        pad = _image_point(hole, board, side, source, target)
        width = metrics.horizontalAdvance(text) + 10
        height = metrics.height() + 4
        choices = (
            QRectF(pad.x() + x * (width / 2 + 5) - width / 2,
                   pad.y() + y * (height / 2 + 8) - height / 2, width, height)
            for x, y in offsets if x or y
        )
        box = next(
            (choice for choice in choices if field.contains(choice)
             and not choice.adjusted(-2, -2, 2, 2).contains(pad)
             and not any(choice.adjusted(-2, -2, 2, 2).intersects(other) for other in placed)),
            None,
        )
        if box is None:
            # shortcut: crowded images can overlap tags; the action list retains every address.
            box = QRectF(min(max(field.left(), pad.x() + 7), field.right() - width),
                         min(max(field.top(), pad.y() - height), field.bottom() - height),
                         width, height)
        placed.append(box)
        ink = QColor("#ffdb69" if active else "#cbd5e1")
        painter.setPen(QPen(ink, 1.4))
        painter.drawLine(pad, box.center())
        painter.setBrush(QColor("#222328" if active else "#242b35"))
        painter.drawRoundedRect(box, 4, 4)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    painter.setPen(QPen(QColor("#ffffff"), 1.8))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(a1, 8, 8)
    orientation = say("Copper side · A1 at upper right" if side == "bottom" else
                      "Component side · A1 at upper left")
    painter.drawText(QRectF(24, 4, target.width() - 48, 25), Qt.AlignmentFlag.AlignCenter, orientation)


def _image_point(
    hole: HoleCoord, board: Board, side: BoardSide, source: QRectF, target: QRectF,
) -> QPointF:
    point = hole_to_screen(hole, board, side)
    return QPointF(
        (point.x() - source.x()) * target.width() / source.width(),
        (point.y() - source.y()) * target.height() / source.height(),
    )


def render_guide_images(
    doc: PerfDocument,
    guide: Guide,
    lookup: FootprintLookup,
    progress: Callable[[int, int], bool] | None = None,
) -> dict[str, bytes]:
    """Draw cumulative and isolated action views, and one finished view per face."""
    guide = replace(guide, phases=assembly_phases(guide))
    frames: list[tuple[int, bool, tuple[GuideStep, ...], str]] = []
    for index, step in enumerate(all_steps(guide)):
        bottom = step.mirrored if isinstance(step, PartStep) else step.side == "bottom"
        frames.append((index, bottom, (step,), f"action:{step_focus(step)}"))

    frames += [(index, bottom, work, f"only:{key}") for index, bottom, work, key in frames]
    frames += [(-1, bottom, (), f"finished:{int(bottom)}") for bottom in dict.fromkeys(
        bottom for _, bottom, _, _ in frames
    )]

    outline = board_outline_mm(doc.board)
    margin = RULER_MARGIN_MM
    source = QRectF(outline.x, outline.y, outline.width, outline.height).adjusted(
        -margin, -margin, margin, margin
    )
    scale = 32 / doc.board.pitch
    extra_width = max(0, 360 / scale - source.width()) / 2
    source.adjust(-extra_width, 0, extra_width, 0)
    width, height = round(source.width() * scale), round(source.height() * scale)
    target = QRectF(0, 0, width, height)
    say = phrasebook(guide.language)
    images: dict[str, bytes] = {}
    for index, bottom, work, key in frames:
        current = doc if index == -1 else document_at_step(doc, guide, index)
        if key.startswith("only:"):
            active = {step_focus(step) for step in work}
            current = replace(
                current,
                components=tuple(part for part in current.components if part.id in active),
                conductors=tuple(wire for wire in current.conductors if wire.id in active),
            )
        side: BoardSide = "bottom" if bottom else "top"
        scene = BoardScene(
            current, lookup, side=side, show_ratsnest=False,
            show_rulers=False, show_pin_names=False,
        )
        for item in scene.component_items.values():
            if item.comp.mirrored:
                item.mount_side = "bottom"
            if not work:
                item.setOpacity(0.25)
        scene.addItem(HoleRulerItem(doc.board, side, label_px=16))
        if work:
            scene.set_problem_focus(
                (),
                tuple(step_focus(step) for step in work if not isinstance(step, PartStep)),
                tuple(step_focus(step) for step in work if isinstance(step, PartStep)),
            )
        image = QImage(width, height, QImage.Format.Format_RGB32)
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            scene.render(painter, target, source, Qt.AspectRatioMode.IgnoreAspectRatio)
            _annotate(painter, current, side, work, source, target, say)
        finally:
            painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        writer = QImageWriter(buffer, b"png")
        if not writer.write(image):
            raise RuntimeError(writer.errorString())
        images[key] = bytes(buffer.data().data())
        if progress is not None and not progress(len(images), len(frames)):
            return {}
    return images
