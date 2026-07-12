"""Key sequence capture widget.

Qt's QKeySequenceEdit drops KeyboardModifier.KeypadModifier, so numpad digits
are stored as the same binding as the number row (``9`` instead of ``Num+9``).
We preserve the keypad modifier so both keys can be bound independently.
"""

from PyQt6.QtCore import QKeyCombination, Qt
from PyQt6.QtGui import QKeyEvent, QKeySequence
from PyQt6.QtWidgets import QKeySequenceEdit

_KEYPAD_MODIFIERS = (
    Qt.KeyboardModifier.ShiftModifier,
    Qt.KeyboardModifier.ControlModifier,
    Qt.KeyboardModifier.AltModifier,
    Qt.KeyboardModifier.MetaModifier,
    Qt.KeyboardModifier.KeypadModifier,
)


def key_event_to_sequence(event: QKeyEvent) -> QKeySequence | None:
    """Build a portable QKeySequence from a key event, preserving numpad keys."""
    if event.key() in (Qt.Key.Key_unknown,):
        return None
    if not event.modifiers() & Qt.KeyboardModifier.KeypadModifier:
        return None

    mods = Qt.KeyboardModifier.NoModifier
    for modifier in _KEYPAD_MODIFIERS:
        if event.modifiers() & modifier:
            mods |= modifier
    return QKeySequence(QKeyCombination(mods, Qt.Key(event.key())))


class KeypadAwareKeySequenceEdit(QKeySequenceEdit):
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Backspace:
            super().keyPressEvent(event)
            return

        sequence = key_event_to_sequence(event)
        if sequence is not None:
            self.setKeySequence(sequence)
            event.accept()
            return

        super().keyPressEvent(event)
