import sys
from PySide6.QtWidgets import QApplication
from maps3d_app.ui.main_window import MainWindow

def run() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

def main() -> None:
    run()
