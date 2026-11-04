from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from mkboota.models import SegmentSpec


class TimelineWidget(QWidget):
    """Multi-part timeline editor widget for SegmentSpec sequences"""

    segments_changed = Signal(list)
    segment_selected = Signal(int)
    time_scrubbed = Signal(float)
    segment_time_changed = Signal(int, float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[SegmentSpec] = []
        self._duration: float = 0.0
        self._current_time: float = 0.0
        self._selected_index: int = -1
        self._dragging_cut_index: int | None = None
        self._drag_last_x: float = 0.0
        self.setMouseTracking(True)
        self.setMinimumHeight(56)

    def set_duration(self, seconds: float) -> None:
        self._duration = max(0.0, float(seconds))
        if self._current_time > self._duration:
            self._current_time = self._duration
        self.update()

    def get_duration(self) -> float:
        return self._duration

    def get_segments(self) -> list[SegmentSpec]:
        return list(self._segments)

    def set_segments(self, segments: Sequence[SegmentSpec]) -> None:
        self._segments = list(segments)
        if self._selected_index >= len(self._segments):
            self._selected_index = -1
        self.segments_changed.emit(self.get_segments())
        self.update()

    def add_segment(self, spec: SegmentSpec) -> None:
        self._segments.append(spec)
        self._segments.sort(key=lambda item: item.start_time)
        self.segments_changed.emit(self.get_segments())
        self.update()

    def split_at(self, timestamp: float) -> None:
        if timestamp <= 0.0:
            return

        if self._duration > 0.0 and timestamp >= self._duration:
            return

        target_idx: int | None = None
        for idx, seg in enumerate(self._segments):
            end = seg.end_time if seg.end_time is not None else self._duration
            if seg.start_time < timestamp < end:
                target_idx = idx
                break

        if target_idx is None:
            return

        target = self._segments[target_idx]
        seg1 = SegmentSpec(
            start_time=target.start_time,
            end_time=timestamp,
            type_code=target.type_code,
            count=target.count,
            pause=target.pause,
            folder_name=target.folder_name,
            bg_color=target.bg_color,
            clock_pos=target.clock_pos,
        )
        seg2 = SegmentSpec(
            start_time=timestamp,
            end_time=target.end_time,
            type_code=target.type_code,
            count=target.count,
            pause=target.pause,
            folder_name=None,
            bg_color=target.bg_color,
            clock_pos=target.clock_pos,
        )

        self._segments[target_idx : target_idx + 1] = [seg1, seg2]
        self._selected_index = target_idx + 1
        self.segments_changed.emit(self.get_segments())
        self.segment_selected.emit(self._selected_index)
        self.update()

    def delete_segment(self, index: int, ripple: bool = True) -> None:
        if len(self._segments) <= 1:
            return

        if index < 0 or index >= len(self._segments):
            return

        target = self._segments[index]
        target_end = target.end_time if target.end_time is not None else self._duration
        span = max(0.0, target_end - target.start_time)

        del self._segments[index]

        if ripple and span > 0.0:
            self._segments[index:] = [
                self._shift_segment(seg, span) for seg in self._segments[index:]
            ]

        if not self._segments:
            self._selected_index = -1
        elif index < self._selected_index:
            self._selected_index -= 1
        elif self._selected_index >= len(self._segments):
            self._selected_index = len(self._segments) - 1

        self.segments_changed.emit(self.get_segments())
        self.segment_selected.emit(self._selected_index)
        self.update()

    @staticmethod
    def _shift_segment(seg: SegmentSpec, span: float) -> SegmentSpec:
        new_end = (seg.end_time - span) if seg.end_time is not None else None
        return replace(seg, start_time=max(0.0, seg.start_time - span), end_time=new_end)

    def merge_with_next(self, index: int) -> None:
        if index < 0 or index >= len(self._segments) - 1:
            return

        current_seg = self._segments[index]
        next_seg = self._segments[index + 1]
        merged_seg = replace(current_seg, end_time=next_seg.end_time)

        self._segments[index] = merged_seg
        del self._segments[index + 1]

        if self._selected_index > index + 1:
            self._selected_index -= 1
        elif self._selected_index == index + 1:
            self._selected_index = index

        self.segments_changed.emit(self.get_segments())
        self.segment_selected.emit(self._selected_index)
        self.update()

    def adjust_cut_point(self, index: int, new_time: float) -> None:
        if index < 0 or index >= len(self._segments) - 1:
            return

        left_seg = self._segments[index]
        right_seg = self._segments[index + 1]

        min_time = left_seg.start_time + 0.1
        next_end = right_seg.end_time if right_seg.end_time is not None else self._duration
        if next_end <= min_time:
            return

        max_time = next_end - 0.1 if next_end > 0.0 else min_time
        if max_time < min_time:
            max_time = min_time

        clamped_time = max(min_time, min(new_time, max_time))

        self._segments[index] = replace(left_seg, end_time=clamped_time)
        self._segments[index + 1] = replace(right_seg, start_time=clamped_time)

        self.segments_changed.emit(self.get_segments())
        self.segment_time_changed.emit(index, left_seg.start_time, clamped_time)
        self.segment_time_changed.emit(index + 1, clamped_time, next_end)
        self.update()

    def select_segment(self, index: int) -> None:
        if 0 <= index < len(self._segments):
            self._selected_index = index
        else:
            self._selected_index = -1

        self.segment_selected.emit(self._selected_index)
        self.update()

    def get_selected_index(self) -> int:
        return self._selected_index

    def set_current_time(self, seconds: float) -> None:
        clamped = max(0.0, min(float(seconds), self._duration))
        self._current_time = clamped
        self.time_scrubbed.emit(clamped)
        self.update()

    def get_current_time(self) -> float:
        return self._current_time

    def _hit_test_cut_point(self, pos_x: float) -> int | None:
        if self._duration <= 0.0 or self.width() <= 0:
            return None

        for idx in range(len(self._segments) - 1):
            seg = self._segments[idx]
            cut_time = (
                seg.end_time if seg.end_time is not None else self._segments[idx + 1].start_time
            )
            boundary_x = (cut_time / self._duration) * self.width()
            if abs(pos_x - boundary_x) <= 5.0:
                return idx

        return None

    def _handle_cut_drag(self, pos_x: float) -> None:
        if self._dragging_cut_index is None or self._duration <= 0.0 or self.width() <= 0:
            return

        delta_x = pos_x - self._drag_last_x
        self._drag_last_x = pos_x
        delta_t = (delta_x / self.width()) * self._duration
        curr_seg = self._segments[self._dragging_cut_index]
        curr_time = (
            curr_seg.end_time
            if curr_seg.end_time is not None
            else self._segments[self._dragging_cut_index + 1].start_time
        )
        time_val = curr_time + delta_t

        left_seg = self._segments[self._dragging_cut_index]
        right_seg = self._segments[self._dragging_cut_index + 1]

        min_time = left_seg.start_time + 0.1
        next_end = right_seg.end_time if right_seg.end_time is not None else self._duration
        if next_end <= min_time:
            return

        max_time = next_end - 0.1 if next_end > 0.0 else min_time
        if max_time < min_time:
            max_time = min_time

        clamped_time = max(min_time, min(max_time, time_val))
        self.adjust_cut_point(self._dragging_cut_index, clamped_time)
        self._current_time = clamped_time
        self.time_scrubbed.emit(clamped_time)
        self.update()

    @staticmethod
    def _resolve_mode_name(seg: SegmentSpec) -> str:
        if seg.type_code == "c":
            return "Complete"

        if seg.count == 0:
            return "Loop"

        if seg.count == 1:
            return "Standard"

        return "Repeat"

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos_x = event.position().x()
        hit_cut = self._hit_test_cut_point(pos_x)
        if hit_cut is not None:
            self._dragging_cut_index = hit_cut
            self._drag_last_x = pos_x
            return

        self._handle_scrub(pos_x)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging_cut_index is not None:
            self._handle_cut_drag(event.position().x())
            return

        if not (event.buttons() & Qt.MouseButton.LeftButton):
            hit_cut = self._hit_test_cut_point(event.position().x())
            cursor_shape = (
                Qt.CursorShape.SizeHorCursor if hit_cut is not None else Qt.CursorShape.ArrowCursor
            )
            self.setCursor(cursor_shape)
            return

        self._handle_scrub(event.position().x())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging_cut_index is not None:
            self._dragging_cut_index = None
            hit_cut = self._hit_test_cut_point(event.position().x())
            cursor_shape = (
                Qt.CursorShape.SizeHorCursor if hit_cut is not None else Qt.CursorShape.ArrowCursor
            )
            self.setCursor(cursor_shape)

    def leaveEvent(self, event: QEvent) -> None:
        if self._dragging_cut_index is None:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def _handle_scrub(self, pos_x: float) -> None:
        if self._duration <= 0.0 or self.width() <= 0:
            return

        ratio = max(0.0, min(1.0, pos_x / self.width()))
        time_val = ratio * self._duration
        self.set_current_time(time_val)

        for idx, seg in enumerate(self._segments):
            end = seg.end_time if seg.end_time is not None else self._duration
            if seg.start_time <= time_val <= end:
                self.select_segment(idx)
                break

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        width = self.width()
        height = self.height()

        painter.fillRect(0, 0, width, height, QColor("#1e1e24"))

        if not self._segments or self._duration <= 0.0:
            painter.setPen(QPen(QColor("#383842"), 1))
            painter.drawRect(0, 0, max(0, width - 1), max(0, height - 1))
            return

        track_height = max(20, height - 16)
        track_y = 8

        for idx, seg in enumerate(self._segments):
            end = seg.end_time if seg.end_time is not None else self._duration
            x1 = int((seg.start_time / self._duration) * width)
            x2 = int((end / self._duration) * width)
            w = max(2, x2 - x1)

            is_selected = idx == self._selected_index
            fill_color = QColor("#383842") if is_selected else QColor("#282830")
            painter.fillRect(x1, track_y, w, track_height, fill_color)

            border_color = QColor("#4a90e2") if is_selected else QColor("#383842")
            painter.setPen(QPen(border_color, 1))
            painter.drawRect(x1, track_y, w, track_height)

            name = seg.folder_name or f"part{idx}"
            mode = self._resolve_mode_name(seg)
            label = f"{name} [{mode}]"
            painter.setPen(QColor("#ffffff" if is_selected else "#8a8a96"))
            painter.drawText(
                QRectF(float(x1 + 4), float(track_y), max(10.0, float(w - 8)), float(track_height)),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label,
            )

        for idx in range(len(self._segments) - 1):
            seg = self._segments[idx]
            cut_time = (
                seg.end_time if seg.end_time is not None else self._segments[idx + 1].start_time
            )
            cut_x = int((cut_time / self._duration) * width)
            painter.setPen(QPen(QColor("#606070"), 2))
            painter.drawLine(cut_x, track_y + 2, cut_x, track_y + track_height - 2)

        playhead_x = int((self._current_time / self._duration) * width)
        painter.setPen(QPen(QColor("#4a90e2"), 2))
        painter.drawLine(playhead_x, 0, playhead_x, height)
