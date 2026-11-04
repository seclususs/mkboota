from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from mkboota.editor import (
    DEFAULT_CANVAS,
    CropperUIProtocol,
    EditorResult,
    _make_even_dimension,
)
from mkboota.exceptions import CropCancelledError, InvalidCropError
from mkboota.models import CanvasResolution, CropRect


class StudioEditorDialog(QDialog):
    """Qt dialog for aspect-ratio cropping and timeline duration trimming"""

    def __init__(
        self,
        preview_image: Image.Image,
        canvas: CanvasResolution = DEFAULT_CANVAS,
        video_duration: float = 0.0,
        fps: int = 30,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.preview_image = preview_image
        self.canvas = canvas
        self.video_duration = max(0.0, video_duration)
        self.fps = max(1, fps)
        self.start_time = 0.0
        self.duration = min(self.video_duration, 8.0) if self.video_duration > 0.0 else 0.0

        self.orig_w, self.orig_h = preview_image.size
        if self.orig_w <= 0 or self.orig_h <= 0:
            raise InvalidCropError(
                f"Preview image dimensions must be positive: {self.orig_w}x{self.orig_h}"
            )

        self.setWindowTitle("Crop & Trim")

        img_rgba = preview_image.convert("RGBA")
        qimage = QImage(
            img_rgba.tobytes("raw", "RGBA"),
            self.orig_w,
            self.orig_h,
            QImage.Format.Format_RGBA8888,
        )
        self.pixmap = QPixmap.fromImage(qimage)

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            max_w = max(400.0, avail.width() * 0.65)
            max_h = max(300.0, avail.height() * 0.65)
        else:
            max_w = 800.0
            max_h = 600.0

        scale = min(max_w / self.orig_w, max_h / self.orig_h)
        disp_w = max(160.0, self.orig_w * scale)
        disp_h = max(120.0, self.orig_h * scale)

        self.hud_height = 76.0 if self.video_duration > 0.0 else 32.0

        legend_line = (
            "Drag Handles: In/Out Trim  |  Drag Bar: Move  |  "
            "Scroll: Fine-Tune  |  Space: Center Crop  |  Enter: Build"
            if self.video_duration > 0.0
            else "Space: Center Crop  |  Enter: Confirm  |  Esc: Cancel  |  Scroll: Resize Crop"
        )
        legend_w = self.fontMetrics().horizontalAdvance(legend_line)
        min_hud_w = max(680, legend_w + 48)
        if screen is not None:
            min_hud_w = min(int(avail.width() * 0.95), min_hud_w)

        dialog_w = max(int(disp_w), min_hud_w)
        dialog_h = int(disp_h + self.hud_height)

        offset_x = (float(dialog_w) - disp_w) / 2.0
        self.image_rect = QRectF(offset_x, 0.0, disp_w, disp_h)

        self.resize(dialog_w, dialog_h)
        self.setMinimumSize(min(dialog_w, min_hud_w), max(180, int(disp_h * 0.5 + self.hud_height)))

        target_r = self.canvas.aspect_ratio
        box_r = disp_w / disp_h
        if box_r > target_r:
            init_h = disp_h
            init_w = disp_h * target_r
        else:
            init_w = disp_w
            init_h = disp_w / target_r

        init_x = self.image_rect.left() + (disp_w - init_w) / 2.0
        init_y = self.image_rect.top() + (disp_h - init_h) / 2.0
        self.crop_box = QRectF(init_x, init_y, init_w, init_h)

        self._is_dragging = False
        self._is_dragging_timeline = False
        self._timeline_drag_mode: str | None = None
        self._timeline_drag_start_x: float = 0.0
        self._timeline_drag_start_time: float = 0.0
        self._timeline_drag_start_dur: float = 0.0
        self._drag_start_pos = QPointF()
        self._drag_start_box = QRectF()
        self.setMouseTracking(True)

    def set_trim_range(self, start: float, duration: float) -> None:
        self.start_time = max(0.0, start)

        if self.video_duration > 0:
            max_avail = max(0.1, self.video_duration - self.start_time)
            self.duration = min(duration, max_avail)
        else:
            self.duration = max(0.1, duration)

        self.update()

    def center_crop_box(self) -> None:
        center_x = self.image_rect.left() + (self.image_rect.width() - self.crop_box.width()) / 2.0
        center_y = self.image_rect.top() + (self.image_rect.height() - self.crop_box.height()) / 2.0
        self.crop_box.moveTo(center_x, center_y)
        self.update()

    def get_crop_rect(self) -> CropRect:
        if self.image_rect.width() <= 0 or self.image_rect.height() <= 0:
            return CropRect(0, 0, self.orig_w, self.orig_h)

        scale_x = self.orig_w / self.image_rect.width()
        scale_y = self.orig_h / self.image_rect.height()

        orig_x = round((self.crop_box.x() - self.image_rect.x()) * scale_x)
        orig_y = round((self.crop_box.y() - self.image_rect.y()) * scale_y)
        orig_w = round(self.crop_box.width() * scale_x)
        orig_h = round(self.crop_box.height() * scale_y)

        orig_x = max(0, min(self.orig_w - 1, orig_x))
        orig_y = max(0, min(self.orig_h - 1, orig_y))
        orig_w = max(1, min(self.orig_w - orig_x, orig_w))
        orig_h = max(1, min(self.orig_h - orig_y, orig_h))

        orig_w = _make_even_dimension(orig_w, self.orig_w - orig_x)
        orig_h = _make_even_dimension(orig_h, self.orig_h - orig_y)

        return CropRect(x=orig_x, y=orig_y, width=orig_w, height=orig_h)

    def get_result(self) -> EditorResult:
        crop = self.get_crop_rect()
        return EditorResult(
            x=crop.x,
            y=crop.y,
            width=crop.width,
            height=crop.height,
            start_time=self.start_time,
            duration=self.duration if self.video_duration > 0.0 else None,
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        avail_w = float(self.width())
        avail_h = max(100.0, float(self.height()) - self.hud_height)
        scale = min(avail_w / self.orig_w, avail_h / self.orig_h)
        disp_w = max(100.0, self.orig_w * scale)
        disp_h = max(100.0, self.orig_h * scale)

        new_x = (avail_w - disp_w) / 2.0
        new_y = (avail_h - disp_h) / 2.0
        old_image_rect = QRectF(self.image_rect)
        self.image_rect = QRectF(new_x, new_y, disp_w, disp_h)

        if old_image_rect.width() <= 0 or old_image_rect.height() <= 0:
            return

        scale_box_w = disp_w / old_image_rect.width()
        scale_box_h = disp_h / old_image_rect.height()
        box_rel_x = (self.crop_box.x() - old_image_rect.x()) * scale_box_w
        box_rel_y = (self.crop_box.y() - old_image_rect.y()) * scale_box_h
        new_box_w = self.crop_box.width() * scale_box_w
        new_box_h = self.crop_box.height() * scale_box_h

        target_r = self.canvas.aspect_ratio
        if abs(new_box_w / new_box_h - target_r) > 1e-3:
            new_box_h = new_box_w / target_r

        new_box_x = max(new_x, min(new_x + disp_w - new_box_w, new_x + box_rel_x))
        new_box_y = max(new_y, min(new_y + disp_h - new_box_h, new_y + box_rel_y))
        self.crop_box = QRectF(new_box_x, new_box_y, new_box_w, new_box_h)

    @staticmethod
    def _fill_overlay_rect(
        painter: QPainter,
        x: float,
        y: float,
        w: float,
        h: float,
        color: QColor,
    ) -> None:
        if w > 0 and h > 0:
            painter.fillRect(QRectF(x, y, w, h), color)

    def _paint_mask_overlays(self, painter: QPainter) -> None:
        overlay_color = QColor(0, 0, 0, 160)
        im = self.image_rect
        cb = self.crop_box
        self._fill_overlay_rect(
            painter, im.left(), im.top(), im.width(), cb.top() - im.top(), overlay_color
        )
        self._fill_overlay_rect(
            painter, im.left(), cb.bottom(), im.width(), im.bottom() - cb.bottom(), overlay_color
        )
        self._fill_overlay_rect(
            painter, im.left(), cb.top(), cb.left() - im.left(), cb.height(), overlay_color
        )
        self._fill_overlay_rect(
            painter, cb.right(), cb.top(), im.right() - cb.right(), cb.height(), overlay_color
        )

    def _paint_grid_lines(self, painter: QPainter) -> None:
        grid_pen = QPen(QColor(255, 255, 255, 120), 1.0, Qt.PenStyle.DashLine)
        painter.setPen(grid_pen)
        third_w = self.crop_box.width() / 3.0
        third_h = self.crop_box.height() / 3.0

        v1_x = self.crop_box.left() + third_w
        v2_x = self.crop_box.left() + 2 * third_w
        painter.drawLine(QPointF(v1_x, self.crop_box.top()), QPointF(v1_x, self.crop_box.bottom()))
        painter.drawLine(QPointF(v2_x, self.crop_box.top()), QPointF(v2_x, self.crop_box.bottom()))

        h1_y = self.crop_box.top() + third_h
        h2_y = self.crop_box.top() + 2 * third_h
        painter.drawLine(QPointF(self.crop_box.left(), h1_y), QPointF(self.crop_box.right(), h1_y))
        painter.drawLine(QPointF(self.crop_box.left(), h2_y), QPointF(self.crop_box.right(), h2_y))

        border_pen = QPen(QColor(0, 180, 255), 2.0, Qt.PenStyle.SolidLine)
        painter.setPen(border_pen)
        painter.drawRect(self.crop_box)

    def _get_timeline_geometry(self) -> tuple[QRectF, float, float, QRectF, QRectF]:
        hud_top = float(self.height()) - self.hud_height
        track_w = max(1.0, float(self.width()) - 32.0)
        track_rect = QRectF(16.0, hud_top + 10.0, track_w, 8.0)

        if self.video_duration <= 0.0:
            dummy_rect = QRectF()
            return track_rect, 16.0, 16.0, dummy_rect, dummy_rect

        start_ratio = min(1.0, max(0.0, self.start_time / self.video_duration))
        end_ratio = min(1.0, max(0.0, (self.start_time + self.duration) / self.video_duration))
        sel_x = track_rect.left() + start_ratio * track_w
        sel_right = track_rect.left() + end_ratio * track_w

        in_handle = QRectF(sel_x - 4.0, track_rect.top() - 4.0, 8.0, 16.0)
        out_handle = QRectF(sel_right - 4.0, track_rect.top() - 4.0, 8.0, 16.0)
        return track_rect, sel_x, sel_right, in_handle, out_handle

    def _hit_test_timeline(self, pos: QPointF) -> str | None:
        if self.video_duration <= 0.0:
            return None

        hud_top = float(self.height()) - self.hud_height
        if pos.y() < hud_top:
            return None

        track_rect, sel_x, sel_right, _, _ = self._get_timeline_geometry()
        pos_x = pos.x()

        if abs(pos_x - sel_right) <= 8.0:
            return "out"

        if abs(pos_x - sel_x) <= 8.0:
            return "in"

        if sel_x < pos_x < sel_right:
            return "slide"

        if track_rect.left() - 4.0 <= pos_x <= track_rect.right() + 4.0:
            return "seek"

        return None

    def _paint_hud(self, painter: QPainter, hud_rect: QRectF) -> None:
        painter.fillRect(hud_rect, QColor(24, 26, 30))
        painter.setPen(QPen(QColor(48, 52, 60), 1.0))
        painter.drawLine(hud_rect.topLeft(), hud_rect.topRight())

        if self.video_duration <= 0.0:
            painter.setPen(QColor(150, 155, 165))
            legend_line = (
                "Space: Center Crop  |  Enter: Confirm  |  Esc: Cancel  |  Scroll: Resize Crop"
            )
            painter.drawText(QPointF(16.0, hud_rect.top() + 20.0), legend_line)
            return

        track_rect, sel_x, sel_right, in_handle, out_handle = self._get_timeline_geometry()
        sel_w = max(4.0, sel_right - sel_x)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(42, 46, 54))
        painter.drawRoundedRect(track_rect, 4.0, 4.0)

        painter.setBrush(QColor(0, 180, 255))
        painter.drawRoundedRect(
            QRectF(sel_x, track_rect.top(), sel_w, track_rect.height()), 3.0, 3.0
        )

        painter.setBrush(QColor(245, 248, 255))
        painter.setPen(QPen(QColor(0, 150, 220), 1.5))
        painter.drawRoundedRect(in_handle, 2.0, 2.0)
        painter.drawRoundedRect(out_handle, 2.0, 2.0)

        frames = round(self.duration * self.fps)
        est_mb = frames * 0.035

        if self.duration <= 8.0 and est_mb <= 25.0:
            health_str = "Safe"
        elif self.duration <= 15.0:
            health_str = "Caution"
        else:
            health_str = "Hazard"

        painter.setPen(QColor(220, 225, 230))
        info_line = (
            f"In: {self.start_time:.1f}s   Out: {self.start_time + self.duration:.1f}s   "
            f"Selected: {self.duration:.1f}s ({frames} frames @ {self.fps}fps)   "
            f"Est Size: ~{est_mb:.1f} MB [{health_str}]"
        )
        painter.drawText(QPointF(16.0, hud_rect.top() + 36.0), info_line)

        painter.setPen(QColor(130, 136, 148))
        legend_line = (
            "Drag Handles: In/Out Trim  |  Drag Bar: Move  |  "
            "Scroll: Fine-Tune  |  Space: Center Crop  |  Enter: Build"
        )
        painter.drawText(QPointF(16.0, hud_rect.top() + 56.0), legend_line)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)

        try:
            painter.fillRect(self.rect(), QColor(16, 18, 20))
            painter.drawPixmap(self.image_rect.toRect(), self.pixmap)
            self._paint_mask_overlays(painter)
            self._paint_grid_lines(painter)
            hud_rect = QRectF(
                0.0, float(self.height()) - self.hud_height, float(self.width()), self.hud_height
            )
            self._paint_hud(painter, hud_rect)
        finally:
            painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        pos = event.position()
        if self.crop_box.contains(pos):
            self._is_dragging = True
            self._drag_start_pos = pos
            self._drag_start_box = QRectF(self.crop_box)
            event.accept()
            return

        mode = self._hit_test_timeline(pos)
        if mode is not None and self.video_duration > 0.0:
            self._timeline_drag_mode = mode
            self._timeline_drag_start_x = pos.x()
            self._timeline_drag_start_time = self.start_time
            self._timeline_drag_start_dur = self.duration
            self._is_dragging_timeline = True

            track_rect, _, _, _, _ = self._get_timeline_geometry()
            track_w = track_rect.width()

            if mode == "seek":
                click_x = max(0.0, min(track_w, pos.x() - track_rect.left()))
                new_start = (click_x / track_w) * self.video_duration
                max_start = max(0.0, self.video_duration - self.duration)
                self.set_trim_range(min(new_start, max_start), self.duration)
            elif mode == "out":
                click_x = max(0.0, min(track_w, pos.x() - track_rect.left()))
                click_time = (click_x / track_w) * self.video_duration
                new_dur = max(
                    0.1, min(self.video_duration - self.start_time, click_time - self.start_time)
                )
                self.set_trim_range(self.start_time, new_dur)
            elif mode == "in":
                click_x = max(0.0, min(track_w, pos.x() - track_rect.left()))
                click_time = (click_x / track_w) * self.video_duration
                end_time = self.start_time + self.duration
                clamped_start = max(0.0, min(end_time - 0.1, click_time))
                self.set_trim_range(clamped_start, end_time - clamped_start)

            self.update()
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._is_dragging:
            delta = event.position() - self._drag_start_pos
            new_x = self._drag_start_box.x() + delta.x()
            new_y = self._drag_start_box.y() + delta.y()

            new_x = max(
                self.image_rect.left(),
                min(self.image_rect.right() - self.crop_box.width(), new_x),
            )
            new_y = max(
                self.image_rect.top(),
                min(self.image_rect.bottom() - self.crop_box.height(), new_y),
            )

            self.crop_box.moveTo(new_x, new_y)
            self.update()
            event.accept()
            return

        if self._is_dragging_timeline and self.video_duration > 0.0 and self._timeline_drag_mode:
            track_rect, _, _, _, _ = self._get_timeline_geometry()
            track_w = track_rect.width()
            pos_x = event.position().x()

            if self._timeline_drag_mode == "out":
                click_x = max(0.0, min(track_w, pos_x - track_rect.left()))
                cur_time = (click_x / track_w) * self.video_duration
                new_dur = max(
                    0.1, min(self.video_duration - self.start_time, cur_time - self.start_time)
                )
                self.set_trim_range(self.start_time, new_dur)
            elif self._timeline_drag_mode == "in":
                click_x = max(0.0, min(track_w, pos_x - track_rect.left()))
                cur_time = (click_x / track_w) * self.video_duration
                end_time = self._timeline_drag_start_time + self._timeline_drag_start_dur
                clamped_start = max(0.0, min(end_time - 0.1, cur_time))
                self.set_trim_range(clamped_start, end_time - clamped_start)
            elif self._timeline_drag_mode == "slide":
                delta_x = pos_x - self._timeline_drag_start_x
                delta_time = (delta_x / track_w) * self.video_duration
                max_start = max(0.0, self.video_duration - self.duration)
                new_start = max(0.0, min(max_start, self._timeline_drag_start_time + delta_time))
                self.set_trim_range(new_start, self.duration)
            elif self._timeline_drag_mode == "seek":
                click_x = max(0.0, min(track_w, pos_x - track_rect.left()))
                cur_time = (click_x / track_w) * self.video_duration
                max_start = max(0.0, self.video_duration - self.duration)
                self.set_trim_range(min(cur_time, max_start), self.duration)

            self.update()
            event.accept()
            return

        if not (event.buttons() & Qt.MouseButton.LeftButton) and self.video_duration > 0.0:
            mode = self._hit_test_timeline(event.position())

            if mode in ("out", "in"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif mode == "slide":
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return

        if self._is_dragging:
            self._is_dragging = False
            event.accept()
            return

        if self._is_dragging_timeline:
            self._is_dragging_timeline = False
            self._timeline_drag_mode = None

            mode = self._hit_test_timeline(event.position())
            if mode in ("out", "in"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif mode == "slide":
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

            event.accept()
            return

        super().mouseReleaseEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        if not self._is_dragging_timeline:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return

        pos = event.position()
        if self.video_duration > 0.0 and pos.y() >= float(self.height()) - self.hud_height:
            step = 0.5 if delta > 0 else -0.5
            new_dur = max(0.5, min(self.video_duration - self.start_time, self.duration + step))
            self.set_trim_range(self.start_time, new_dur)
            event.accept()
            return

        factor = 1.05 if delta > 0 else 0.95
        target_r = self.canvas.aspect_ratio

        new_w = self.crop_box.width() * factor
        new_h = new_w / target_r

        min_w = 20.0
        min_h = min_w / target_r
        max_w = self.image_rect.width()
        max_h = self.image_rect.height()

        if max_w / target_r > max_h:
            max_w = max_h * target_r
        else:
            max_h = max_w / target_r

        if new_w < min_w:
            new_w = min_w
            new_h = min_h
        elif new_w > max_w:
            new_w = max_w
            new_h = max_h

        center = self.crop_box.center()
        new_x = center.x() - new_w / 2.0
        new_y = center.y() - new_h / 2.0

        new_x = max(new_x, self.image_rect.left())
        if new_x + new_w > self.image_rect.right():
            new_x = self.image_rect.right() - new_w

        new_y = max(new_y, self.image_rect.top())
        if new_y + new_h > self.image_rect.bottom():
            new_y = self.image_rect.bottom() - new_h

        self.crop_box = QRectF(new_x, new_y, new_w, new_h)
        self.update()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept()
        elif event.key() == Qt.Key.Key_Escape:
            self.reject()
        elif event.key() == Qt.Key.Key_Space:
            self.center_crop_box()
        elif self.video_duration > 0.0 and event.key() == Qt.Key.Key_Up:
            self.set_trim_range(self.start_time, self.duration + 0.5)
        elif self.video_duration > 0.0 and event.key() == Qt.Key.Key_Down:
            self.set_trim_range(self.start_time, self.duration - 0.5)
        elif self.video_duration > 0.0 and event.key() == Qt.Key.Key_Left:
            self.set_trim_range(max(0.0, self.start_time - 0.5), self.duration)
        elif self.video_duration > 0.0 and event.key() == Qt.Key.Key_Right:
            max_s = max(0.0, self.video_duration - self.duration)
            self.set_trim_range(min(max_s, self.start_time + 0.5), self.duration)
        else:
            super().keyPressEvent(event)


class PySide6Cropper(CropperUIProtocol):
    def select_crop(
        self,
        preview_image: Image.Image,
        canvas: CanvasResolution = DEFAULT_CANVAS,
        video_duration: float = 0.0,
        fps: int = 30,
    ) -> EditorResult:
        w_in, h_in = preview_image.size
        if w_in <= 0 or h_in <= 0:
            raise InvalidCropError(f"Preview image dimensions must be positive: {w_in}x{h_in}")

        app = QApplication.instance()
        if app is None:
            app = QApplication(["mkboota"])

        dialog = StudioEditorDialog(
            preview_image=preview_image,
            canvas=canvas,
            video_duration=video_duration,
            fps=fps,
        )
        if dialog.exec() == int(QDialog.DialogCode.Accepted):
            return dialog.get_result()

        raise CropCancelledError("Cropping cancelled")


__all__ = [
    "PySide6Cropper",
    "StudioEditorDialog",
]
