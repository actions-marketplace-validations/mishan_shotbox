#!/usr/bin/env python3
"""A Qt 5 window, "qt-alert", whose Delete button asks in a QMessageBox."""

from PyQt5.QtWidgets import QApplication, QMessageBox, QPushButton, QVBoxLayout, QWidget

app = QApplication([])
w = QWidget(windowTitle="qt-alert")
delete = QPushButton("Delete")
delete.clicked.connect(lambda: QMessageBox.warning(
    w, "Warning", "Delete notes.md?", QMessageBox.Ok | QMessageBox.Cancel))
QVBoxLayout(w).addWidget(delete)
w.show()
app.exec()
