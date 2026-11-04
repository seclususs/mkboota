from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from mkboota.models import CropRect, align_mod16

TOP_LEFT = "TOP_LEFT"
TOP = "TOP"
TOP_RIGHT = "TOP_RIGHT"
RIGHT = "RIGHT"
BOTTOM_RIGHT = "BOTTOM_RIGHT"
BOTTOM = "BOTTOM"
BOTTOM_LEFT = "BOTTOM_LEFT"
LEFT = "LEFT"

_HANDLE_CURSORS: dict[str, Qt.CursorShape] = {
    TOP_LEFT: Qt.CursorShape.SizeFDiagCursor,
    BOTTOM_RIGHT: Qt.CursorShape.SizeFDiagCursor,
    TOP_RIGHT: Qt.CursorShape.SizeBDiagCursor,
    BOTTOM_LEFT: Qt.CursorShape.SizeBDiagCursor,
    LEFT: Qt.CursorShape.SizeHorCursor,
    RIGHT: Qt.CursorShape.SizeHorCursor,
    TOP: Qt.CursorShape.SizeVerCursor,
    BOTTOM: Qt.CursorShape.SizeVerCursor,
}


class CropperCanvas(QWidget):
    crop_changed = Signal(CropRect)

    TOP_LEFT = TOP_LEFT
    TOP = TOP
    TOP_RIGHT = TOP_RIGHT
    RIGHT = RIGHT
    BOTTOM_RIGHT = BOTTOM_RIGHT
    BOTTOM = BOTTOM
    BOTTOM_LEFT = BOTTOM_LEFT
    LEFT = LEFT

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._preset_width: int = 1088
        self._preset_height: int = 1920
        self._orig_width: int = 0
        self._orig_height: int = 0
        self._pixmap: QPixmap | None = None
        self._crop_rect: CropRect = CropRect(x=0, y=0, width=16, height=16)

        self._is_dragging: bool = False
        self._is_resizing: bool = False
        self._active_handle: str | None = None
        self._drag_start_pos: QPointF = QPointF()
        self._drag_start_rect: CropRect = self._crop_rect

        self._aspect_locked: bool = True
        self._zoom: float = 1.0

        self.setMouseTracking(True)
        self.setMinimumSize(200, 200)

    def set_preset(self, width: int, height: int) -> None:
        snapped_w = align_mod16(width) if width > 0 else 16
        self._preset_width = snapped_w
        self._preset_height = max(1, height)

        if self._orig_width > 0 and self._orig_height > 0:
            self._recalculate_crop_from_preset()

    def get_preset_width(self) -> int:
        return self._preset_width

    def get_preset_height(self) -> int:
        return self._preset_height

    def is_aspect_locked(self) -> bool:
        return self._aspect_locked

    def set_aspect_locked(self, locked: bool) -> None:
        self._aspect_locked = locked

    def get_zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float) -> None:
        self._zoom = max(0.2, min(5.0, float(zoom)))
        self.update()

    def set_image(self, image: Image.Image | QPixmap | QImage) -> None:
        if isinstance(image, Image.Image):
            img_rgba = image.convert("RGBA")
            qimg = QImage(
                img_rgba.tobytes("raw", "RGBA"),
                image.width,
                image.height,
                QImage.Format.Format_RGBA8888,
            )
            self._pixmap = QPixmap.fromImage(qimg)
        elif isinstance(image, QImage):
            self._pixmap = QPixmap.fromImage(image)
        elif isinstance(image, QPixmap):
            self._pixmap = image

        if self._pixmap is not None:
            self._orig_width = self._pixmap.width()
            self._orig_height = self._pixmap.height()
            self._zoom = 1.0
            self._recalculate_crop_from_preset()

    def set_frame_image(self, image: QImage | QPixmap) -> None:
        if isinstance(image, QImage):
            self._pixmap = QPixmap.fromImage(image)
        elif isinstance(image, QPixmap):
            self._pixmap = image
        else:
            return

        self._orig_width = self._pixmap.width()
        self._orig_height = self._pixmap.height()
        self.update()

    def get_crop_rect(self) -> CropRect:
        return self._crop_rect

    def set_crop_rect(self, rect: CropRect) -> None:
        w = max(16, (rect.width // 16) * 16)

        if self._orig_width > 0:
            max_stride_w = (self._orig_width // 16) * 16
            w = min(w, max(16, max_stride_w))
            h = min(max(1, rect.height), self._orig_height)
            x = max(0, min(rect.x, self._orig_width - w))
            y = max(0, min(rect.y, self._orig_height - h))
        else:
            x = max(0, rect.x)
            y = max(0, rect.y)
            h = max(1, rect.height)

        self._crop_rect = CropRect(x=x, y=y, width=w, height=h)
        self.crop_changed.emit(self._crop_rect)
        self.update()

    def _recalculate_crop_from_preset(self) -> None:
        target_aspect = self._preset_width / self._preset_height
        img_aspect = self._orig_width / self._orig_height

        if img_aspect > target_aspect:
            crop_h = self._orig_height
            crop_w = int(crop_h * target_aspect)
        else:
            crop_w = self._orig_width
            crop_h = int(crop_w / target_aspect)

        crop_w = max(16, (crop_w // 16) * 16)
        if crop_w > self._orig_width:
            crop_w = max(16, (self._orig_width // 16) * 16)

        crop_h = max(1, int(round(crop_w / target_aspect)))
        if crop_h > self._orig_height:
            crop_h = self._orig_height
            crop_w = max(16, (int(crop_h * target_aspect) // 16) * 16)
            crop_h = max(1, int(round(crop_w / target_aspect)))

        x = max(0, (self._orig_width - crop_w) // 2)
        y = max(0, (self._orig_height - crop_h) // 2)

        self._crop_rect = CropRect(x=x, y=y, width=crop_w, height=crop_h)
        self.crop_changed.emit(self._crop_rect)
        self.update()

    def _get_viewport_rect(self) -> QRectF:
        if self._orig_width <= 0 or self._orig_height <= 0:
            return QRectF(0.0, 0.0, float(self.width()), float(self.height()))

        base_scale = min(self.width() / self._orig_width, self.height() / self._orig_height)
        scale = base_scale * self._zoom
        disp_w = self._orig_width * scale
        disp_h = self._orig_height * scale
        offset_x = (self.width() - disp_w) / 2.0
        offset_y = (self.height() - disp_h) / 2.0
        return QRectF(offset_x, offset_y, disp_w, disp_h)

    def _get_crop_box_rect(self) -> QRectF:
        if self._orig_width <= 0 or self._orig_height <= 0:
            return QRectF()

        vp = self._get_viewport_rect()
        scale = vp.width() / self._orig_width
        box_x = vp.x() + self._crop_rect.x * scale
        box_y = vp.y() + self._crop_rect.y * scale
        box_w = self._crop_rect.width * scale
        box_h = self._crop_rect.height * scale
        return QRectF(box_x, box_y, box_w, box_h)

    def _get_handle_positions(self) -> dict[str, QPointF]:
        r = self._get_crop_box_rect()
        if r.isEmpty():
            return {}

        cx = r.center().x()
        cy = r.center().y()
        return {
            TOP_LEFT: QPointF(r.left(), r.top()),
            TOP: QPointF(cx, r.top()),
            TOP_RIGHT: QPointF(r.right(), r.top()),
            RIGHT: QPointF(r.right(), cy),
            BOTTOM_RIGHT: QPointF(r.right(), r.bottom()),
            BOTTOM: QPointF(cx, r.bottom()),
            BOTTOM_LEFT: QPointF(r.left(), r.bottom()),
            LEFT: QPointF(r.left(), cy),
        }

    def _hit_test_handle(self, x: int | float, y: int | float) -> str | None:
        positions = self._get_handle_positions()
        if not positions:
            return None

        pt = QPointF(float(x), float(y))
        tolerance = 6.0
        ordered_anchors = (
            TOP_LEFT,
            TOP_RIGHT,
            BOTTOM_LEFT,
            BOTTOM_RIGHT,
            TOP,
            BOTTOM,
            LEFT,
            RIGHT,
        )
        for anchor in ordered_anchors:
            pos = positions[anchor]
            if abs(pt.x() - pos.x()) <= tolerance and abs(pt.y() - pos.y()) <= tolerance:
                return anchor

        return None

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._orig_width <= 0 or self._orig_height <= 0:
            return

        delta = event.angleDelta().y()
        if delta == 0:
            return

        factor = 1.1 if delta > 0 else 0.9
        self.set_zoom(self._zoom * factor)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._orig_width <= 0 or self._orig_height <= 0:
            return

        pos = event.position()
        handle = self._hit_test_handle(pos.x(), pos.y())
        if handle is not None:
            self._is_resizing = True
            self._active_handle = handle
            self._drag_start_pos = pos
            self._drag_start_rect = self._crop_rect
            return

        widget_rect = self._get_crop_box_rect()
        if widget_rect.contains(pos):
            self._is_dragging = True
            self._drag_start_pos = pos
            self._drag_start_rect = self._crop_rect

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._orig_width <= 0 or self._orig_height <= 0:
            return

        if self._is_resizing and self._active_handle is not None:
            self._handle_resize_drag(event.position())
            return

        if self._is_dragging:
            self._handle_move_drag(event.position())
            return

        self._update_hover_cursor(event.position())

    def _update_hover_cursor(self, pos: QPointF) -> None:
        handle = self._hit_test_handle(pos.x(), pos.y())
        if handle in _HANDLE_CURSORS:
            self.setCursor(_HANDLE_CURSORS[handle])
            return

        box_rect = self._get_crop_box_rect()
        if box_rect.contains(pos):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            return

        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _handle_move_drag(self, pos: QPointF) -> None:
        vp = self._get_viewport_rect()
        scale = vp.width() / self._orig_width
        if scale <= 0:
            return

        dx = int(round((pos.x() - self._drag_start_pos.x()) / scale))
        dy = int(round((pos.y() - self._drag_start_pos.y()) / scale))

        max_x = max(0, self._orig_width - self._crop_rect.width)
        max_y = max(0, self._orig_height - self._crop_rect.height)
        new_x = max(0, min(self._drag_start_rect.x + dx, max_x))
        new_y = max(0, min(self._drag_start_rect.y + dy, max_y))

        self._crop_rect = CropRect(
            x=new_x,
            y=new_y,
            width=self._crop_rect.width,
            height=self._crop_rect.height,
        )
        self.crop_changed.emit(self._crop_rect)
        self.update()

    def _handle_resize_drag(self, pos: QPointF) -> None:
        if self._active_handle is None:
            return

        vp = self._get_viewport_rect()
        scale = vp.width() / self._orig_width
        if scale <= 0:
            return

        dx = (pos.x() - self._drag_start_pos.x()) / scale
        dy = (pos.y() - self._drag_start_pos.y()) / scale

        new_rect = self._compute_handle_resize(self._active_handle, dx, dy)
        self.set_crop_rect(new_rect)

    def _compute_handle_resize(self, handle: str, dx: float, dy: float) -> CropRect:
        if self._aspect_locked:
            raw_x, raw_y, raw_w, raw_h = self._compute_aspect_resize(handle, dx, dy)
        else:
            raw_x, raw_y, raw_w, raw_h = self._compute_free_resize(handle, dx, dy)

        snapped_w = max(16, int(round(raw_w / 16.0)) * 16)
        clamped_h = max(1, raw_h)

        if self._orig_width > 0:
            max_w = (self._orig_width // 16) * 16
            snapped_w = min(snapped_w, max_w)
            clamped_h = min(clamped_h, self._orig_height)
            clamped_x = max(0, min(raw_x, self._orig_width - snapped_w))
            clamped_y = max(0, min(raw_y, self._orig_height - clamped_h))
        else:
            clamped_x = max(0, raw_x)
            clamped_y = max(0, raw_y)

        return CropRect(x=clamped_x, y=clamped_y, width=snapped_w, height=clamped_h)

    def _compute_aspect_resize(
        self, handle: str, dx: float, dy: float
    ) -> tuple[int, int, int, int]:
        target_aspect = self._preset_width / self._preset_height
        st = self._drag_start_rect

        if handle in (LEFT, RIGHT):
            desired_w = st.width + dx if handle == RIGHT else st.width - dx
            new_w = max(16, int(round(desired_w / 16.0)) * 16)
            new_h = max(1, int(round(new_w / target_aspect)))
            new_x = st.x if handle == RIGHT else st.x + st.width - new_w
            new_y = st.y + (st.height - new_h) // 2
            return new_x, new_y, new_w, new_h

        if handle in (TOP, BOTTOM):
            desired_h = st.height + dy if handle == BOTTOM else st.height - dy
            desired_w = desired_h * target_aspect
            new_w = max(16, int(round(desired_w / 16.0)) * 16)
            new_h = max(1, int(round(new_w / target_aspect)))
            new_x = st.x + (st.width - new_w) // 2
            new_y = st.y if handle == BOTTOM else st.y + st.height - new_h
            return new_x, new_y, new_w, new_h

        delta_w = self._calc_diagonal_delta(handle, dx, dy, target_aspect)
        new_w = max(16, int(round((st.width + delta_w) / 16.0)) * 16)
        new_h = max(1, int(round(new_w / target_aspect)))
        new_x = st.x if handle in (TOP_RIGHT, BOTTOM_RIGHT) else st.x + st.width - new_w
        new_y = st.y if handle in (BOTTOM_LEFT, BOTTOM_RIGHT) else st.y + st.height - new_h
        return new_x, new_y, new_w, new_h

    @staticmethod
    def _calc_diagonal_delta(handle: str, dx: float, dy: float, aspect: float) -> float:
        if handle == BOTTOM_RIGHT:
            return dx if abs(dx) >= abs(dy * aspect) else dy * aspect

        if handle == BOTTOM_LEFT:
            return -dx if abs(-dx) >= abs(dy * aspect) else dy * aspect

        if handle == TOP_RIGHT:
            return dx if abs(dx) >= abs(-dy * aspect) else -dy * aspect

        return -dx if abs(-dx) >= abs(-dy * aspect) else -dy * aspect

    def _compute_free_resize(self, handle: str, dx: float, dy: float) -> tuple[int, int, int, int]:
        st = self._drag_start_rect
        new_x, new_y = st.x, st.y
        new_w, new_h = st.width, st.height

        if handle in (RIGHT, TOP_RIGHT, BOTTOM_RIGHT):
            new_w = max(16, int(round((st.width + dx) / 16.0)) * 16)
        elif handle in (LEFT, TOP_LEFT, BOTTOM_LEFT):
            new_w = max(16, int(round((st.width - dx) / 16.0)) * 16)
            new_x = st.x + st.width - new_w

        if handle in (BOTTOM, BOTTOM_LEFT, BOTTOM_RIGHT):
            new_h = max(1, int(round(st.height + dy)))
        elif handle in (TOP, TOP_LEFT, TOP_RIGHT):
            new_h = max(1, int(round(st.height - dy)))
            new_y = st.y + st.height - new_h

        return new_x, new_y, new_w, new_h

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = False
            self._is_resizing = False
            self._active_handle = None

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(0, 0, self.width(), self.height(), QColor("#1e1e24"))

        if self._pixmap is None or self._orig_width <= 0 or self._orig_height <= 0:
            return

        vp = self._get_viewport_rect()
        painter.drawPixmap(vp.toRect(), self._pixmap)

        box_rect = self._get_crop_box_rect()
        box_x = box_rect.x()
        box_y = box_rect.y()
        box_w = box_rect.width()
        box_h = box_rect.height()

        overlay = QColor(0, 0, 0, 160)
        painter.fillRect(QRectF(vp.x(), vp.y(), vp.width(), box_y - vp.y()), overlay)
        painter.fillRect(
            QRectF(vp.x(), box_y + box_h, vp.width(), vp.bottom() - (box_y + box_h)),
            overlay,
        )
        painter.fillRect(QRectF(vp.x(), box_y, box_x - vp.x(), box_h), overlay)
        painter.fillRect(
            QRectF(box_x + box_w, box_y, vp.right() - (box_x + box_w), box_h),
            overlay,
        )

        painter.setPen(QPen(QColor("#4a90e2"), 2))
        painter.drawRect(box_rect)

        dim_text = f"{self._crop_rect.width}x{self._crop_rect.height}"
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            QRectF(box_x + 6, box_y + 6, max(60.0, box_w - 12), 20.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            dim_text,
        )

        handle_size = 8.0
        half_size = handle_size / 2.0
        painter.setPen(QPen(QColor("#1e1e24"), 1))
        painter.setBrush(QColor("#ffffff"))
        for pt in self._get_handle_positions().values():
            painter.drawRect(
                QRectF(pt.x() - half_size, pt.y() - half_size, handle_size, handle_size)
            )
